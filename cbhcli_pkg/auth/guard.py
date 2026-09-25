"""guard：四端统一登录校验（CLI / Web / Jupyter / exec 共用）

require_login(surface, interactive=True) -> bool
  True=放行；False=未通过（调用方按各端方式拦截）

内部逻辑（对齐设计文档 5.4）：
  1. auth.json 缺失 -> 拦截
  2. 已过期（非永久 token）-> 必须先在线 validate（过期后的无感恢复）：
       成功（管理员已续期）-> 更新本地有效期 -> 放行
       明确 401 -> 拦截（真过期/被吊销/被禁用）
       网络失败 -> 拦截（过期必须在线确认，软在线容忍不适用于已过期 token）
  3. last_validated_at 超 6 小时 -> 在线复核：
       成功 -> 刷新时间 -> 放行
       明确 401 -> 拦截
       网络失败 -> 放行（软在线；记录 offline_since 供严格模式判断）
  4. 严格模式（policy.strict_online=true 且 offline_since 超过 grace）-> 拦截

性能保护：网络失败后 60s 内不重复尝试（_last_online_attempt 内存节流，
避免管理服务宕机时每次消息都卡 3~5 秒）。
"""
from __future__ import annotations

import threading
import time
from typing import Optional

from cbhcli_pkg.auth import guide
from cbhcli_pkg.auth.client import AuthClient, AuthError, normalize_server
from cbhcli_pkg.auth.store import AuthStore, now_iso

_guard_lock = threading.Lock()
_last_reason: str = ""            # 最近一次拦截原因（not_logged_in/expired/...）
_offline_since: Optional[float] = None   # 软在线：最近一次心跳/校验网络失败时间
_last_online_attempt: float = 0.0        # 在线尝试节流（内存）

_OFFLINE_RETRY_THROTTLE = 60.0           # 网络失败后重试间隔（秒）
_DEFAULT_STRICT_GRACE_MIN = 30.0         # 严格模式默认宽限（分钟）


def get_last_reason() -> str:
    """最近一次 require_login 拦截原因（供调用方选文案）。"""
    return _last_reason


def get_offline_since() -> Optional[float]:
    return _offline_since


def _set_reason(reason: str) -> None:
    global _last_reason
    _last_reason = reason


def mark_offline() -> None:
    """记录网络不可达起点（reporter 心跳失败时同步调用）。"""
    global _offline_since
    with _guard_lock:
        if _offline_since is None:
            _offline_since = time.time()


def mark_online() -> None:
    """网络恢复。"""
    global _offline_since
    with _guard_lock:
        _offline_since = None


def _apply_renew(resp: dict, store: AuthStore) -> None:
    """处理 validate/heartbeat 响应中的滚动续期字段。

    - new_token: 剩余有效期 < 2 天时服务端签发的替换 token（透明替换）
    - expires_at: 服务器续期后的最新有效期（无 new_token 时也刷新本地）
    """
    try:
        new_token = resp.get("new_token")
        exp = resp.get("token_expires_at", resp.get("expires_at"))
        if new_token:
            store.update_token(new_token, exp)
        elif exp:
            cur = store.load() or {}
            if exp != cur.get("expires_at"):
                store.update_expiry(exp)
    except Exception:
        pass


def _online_validate(store: AuthStore, surface: str, interactive: bool) -> tuple:
    """在线复核凭据。

    Returns:
        (ok: bool, blocked_reason: str, network_error: bool)
    """
    global _last_online_attempt
    try:
        client = AuthClient(server=store.get_server() or "")
        resp = client.validate(store=store, surface=surface)
        _apply_renew(resp, store)
        store.touch_validated()
        mark_online()
        return True, "", False
    except AuthError as e:
        if e.code in ("unreachable", "timeout", "tls_error", "http_error"):
            _last_online_attempt = time.time()
            mark_offline()
            return False, "offline", True
        # 明确 401：token 失效/被吊销/用户被禁用/协议不符
        _last_online_attempt = time.time()
        return False, (e.code or "invalid_key"), False


def _should_attempt_online() -> bool:
    return (time.time() - _last_online_attempt) > _OFFLINE_RETRY_THROTTLE


def require_login(surface: str = "cli", interactive: bool = True) -> bool:
    """四端统一登录校验（详见模块 docstring）。

    Args:
        surface: "cli" | "web" | "jupyter" | "exec"
        interactive: True=打印交互式引导；False=静默（调用方自行输出）
    Returns:
        True=放行；False=未通过
    """
    store = AuthStore()
    data = store.load()

    # ---- 1. 无凭据 -> 拦截 ----
    if not data:
        _set_reason("not_logged_in")
        if interactive:
            print(guide.login_hint_text(surface))
        return False

    # ---- 2. 已过期（非永久）-> 必须在线确认 ----
    if store.is_expired(data):
        if _should_attempt_online():
            ok, reason, _net = _online_validate(store, surface, interactive)
            if ok:
                return True
            if reason != "offline":
                _set_reason("expired")
                if interactive:
                    print(guide.expired_hint_text(surface))
                return False
            # 网络失败：过期必须在线确认
            _set_reason("expired_offline")
            if interactive:
                print(guide.expired_hint_text(surface))
            return False
        else:
            _set_reason("expired_offline")
            if interactive:
                print(guide.expired_hint_text(surface))
            return False

    # ---- 3. 有效期超 6 小时 -> 在线复核 ----
    if store.needs_validate():
        if _should_attempt_online():
            ok, reason, net_error = _online_validate(store, surface, interactive)
            if ok:
                return True
            if net_error:
                # 软在线：服务不可达时放行（已登录且未过期）
                pass
            else:
                _set_reason("invalid" if reason != "disabled" else "disabled")
                if interactive:
                    if reason == "disabled":
                        print(f"🔐 {guide.error_guide('disabled', surface)}")
                    else:
                        print(guide.expired_hint_text(surface))
                return False

    # ---- 4. 严格模式：离线超宽限 -> 拦截 ----
    policy = data.get("policy") or {}
    if policy.get("strict_online"):
        try:
            grace_min = float(policy.get("strict_grace_minutes",
                                         _DEFAULT_STRICT_GRACE_MIN)
                              or _DEFAULT_STRICT_GRACE_MIN)
        except Exception:
            grace_min = _DEFAULT_STRICT_GRACE_MIN
        since = _offline_since
        if since is not None and (time.time() - since) > grace_min * 60:
            _set_reason("strict_offline")
            if interactive:
                print("🔐 无法连接审计服务器，已按安全策略（strict_online）暂停使用。\n"
                      "   请联系管理员检查管理服务，恢复后自动解除。")
            return False

    _set_reason("")
    return True


def require_login_or_hint(surface: str = "cli") -> bool:
    """CLI 便捷版：未通过时打印对应引导（发消息拦截场景）。"""
    ok = require_login(surface, interactive=False)
    if not ok:
        reason = get_last_reason()
        if reason in ("expired", "expired_offline", "invalid", "disabled", "strict_offline"):
            print(guide.expired_hint_text(surface)
                  if reason != "disabled"
                  else f"🔐 {guide.error_guide('disabled', surface)}")
        else:
            print(guide.login_hint_text(surface))
    return ok
