"""Web 页面访问门禁（页面锁，v5.4.4）

第二层认证：设备已登录 cbhcli 时，浏览器必须输入【本机当前登录账号的认证密钥】
才能访问 Web 页面。目的是解决"设备登录后，任何能访问该端口的人打开浏览器
即可使用 Web 页面"的问题。

设计：
- 设备未登录 -> 门禁不启用（沿用现有登录流程：地址 + 密钥 -> 设备登录）
- 设备已登录 -> 业务 API / WebSocket 均要求页面凭据（中间件 + ws 端点强制）
- 页面凭据：HttpOnly session cookie（关闭浏览器失效）
              + 服务端 24h 滑动过期（超过 24h 无活动即失效）
              + 绝对上限 7 天（防止无限续期）
- 只允许本机当前登录用户的密钥：解锁时校验返回的 user.id == auth.json 的 user.id
- 页面会话只保存在进程内存（服务重启全部失效，浏览器需重新输入）

安全：服务端只存 sha256(token)，cookie 值不落盘；失败限流防爆破。
"""
from __future__ import annotations

import hashlib
import secrets
import threading
import time
from typing import Optional

# Cookie 名（HttpOnly，页面锁凭据）
COOKIE_NAME = "cbhcli_web_gate"

# 服务端会话有效期（滑动过期：每次校验刷新 last_seen）
SESSION_TTL = 24 * 3600
# 绝对上限（防止长期驻留的 cookie 被无限续期）
SESSION_MAX_AGE = 7 * 24 * 3600

# 失败限流：同一 IP 每分钟最多 5 次失败（不计成功）
RATE_LIMIT = 5
RATE_WINDOW = 60.0

_sessions_lock = threading.Lock()
_sessions: dict = {}          # sha256(token) -> {user_id, created, last_seen}

_rate_lock = threading.Lock()
_rate_failures: dict = {}     # ip -> [ts, ...]

# 设备身份（auth.json 的 user_id）2s 短缓存：避免每个 API 请求都读文件
_IDENTITY_TTL = 2.0
_identity_lock = threading.Lock()
_identity_cache = {"ts": 0.0, "mtime": -1, "uid": ""}


# ===================================================================
#  设备状态
# ===================================================================

def gate_enabled() -> bool:
    """是否启用页面锁（= 本机已登录 cbhcli）。"""
    try:
        from cbhcli_pkg.auth import is_logged_in_fast
        return bool(is_logged_in_fast())
    except Exception:
        return False


def _device_user_id() -> str:
    """本机 auth.json 记录的用户 id（带 2s mtime 缓存）。"""
    now = time.time()
    try:
        from cbhcli_pkg.auth import AuthStore
        store = AuthStore()
        p = store.path
        mtime = p.stat().st_mtime_ns if p.exists() else -1
    except Exception:
        return ""
    with _identity_lock:
        if (_identity_cache["mtime"] == mtime
                and now - _identity_cache["ts"] < _IDENTITY_TTL):
            return _identity_cache["uid"]
    uid = ""
    try:
        uid = str((store.get_user() or {}).get("id", "") or "")
    except Exception:
        uid = ""
    with _identity_lock:
        _identity_cache.update({"ts": now, "mtime": mtime, "uid": uid})
    return uid


def token_from_cookie(cookies) -> str:
    """从 cookie 集合安全读取页面令牌。"""
    try:
        return cookies.get(COOKIE_NAME, "") or ""
    except Exception:
        return ""


# ===================================================================
#  会话表
# ===================================================================

def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8", errors="replace")).hexdigest()


def _prune_locked(now: float) -> None:
    expired = [h for h, s in _sessions.items()
               if now - s.get("last_seen", 0) > SESSION_TTL
               or now - s.get("created", 0) > SESSION_MAX_AGE]
    for h in expired:
        _sessions.pop(h, None)


def create_session(user_id: str) -> str:
    """创建页面会话，返回明文 token（只返回一次，服务端存 hash）。"""
    token = secrets.token_urlsafe(32)
    now = time.time()
    with _sessions_lock:
        _sessions[_hash_token(token)] = {
            "user_id": str(user_id or ""),
            "created": now,
            "last_seen": now,
        }
        _prune_locked(now)
    return token


def verify_session(token: str) -> Optional[dict]:
    """校验页面令牌；有效返回会话副本并滑动续期，否则 None。"""
    if not token:
        return None
    h = _hash_token(token)
    now = time.time()
    with _sessions_lock:
        sess = _sessions.get(h)
        if not sess:
            return None
        if (now - sess.get("last_seen", 0) > SESSION_TTL
                or now - sess.get("created", 0) > SESSION_MAX_AGE):
            _sessions.pop(h, None)
            return None
        sess["last_seen"] = now
        out = dict(sess)
    # 设备身份变化（重新登录为其他用户）-> 页面会话失效
    uid = _device_user_id()
    if uid and out.get("user_id") and uid != out["user_id"]:
        revoke_session(token)
        return None
    return out


def revoke_session(token: str) -> bool:
    """吊销单个页面会话。"""
    if not token:
        return False
    with _sessions_lock:
        return _sessions.pop(_hash_token(token), None) is not None


def revoke_all(user_id: str = "") -> int:
    """吊销全部页面会话（user_id 为空=全部；否则仅该用户）。"""
    with _sessions_lock:
        if not user_id:
            n = len(_sessions)
            _sessions.clear()
            return n
        removed = [h for h, s in _sessions.items()
                   if str(s.get("user_id", "")) == str(user_id)]
        for h in removed:
            _sessions.pop(h, None)
        return len(removed)


def is_unlocked(cookies) -> bool:
    """当前请求的 cookie 是否已解锁。"""
    return verify_session(token_from_cookie(cookies)) is not None


# ===================================================================
#  失败限流
# ===================================================================

def rate_blocked(ip: str) -> bool:
    """该 IP 是否已被限流（只读检查，不计数）。"""
    now = time.time()
    with _rate_lock:
        bucket = [t for t in _rate_failures.get(ip or "", [])
                  if now - t < RATE_WINDOW]
        if bucket:
            _rate_failures[ip or ""] = bucket
        else:
            _rate_failures.pop(ip or "", None)
        return len(bucket) >= RATE_LIMIT


def note_failure(ip: str) -> None:
    """记录一次失败尝试。"""
    now = time.time()
    with _rate_lock:
        bucket = [t for t in _rate_failures.get(ip or "", [])
                  if now - t < RATE_WINDOW]
        bucket.append(now)
        _rate_failures[ip or ""] = bucket


def reset_rate(ip: str) -> None:
    """成功后清空该 IP 的失败计数。"""
    with _rate_lock:
        _rate_failures.pop(ip or "", None)


# ===================================================================
#  状态 / 解锁
# ===================================================================

def status(cookies) -> dict:
    """页面锁状态（未解锁时不暴露任何身份信息）。"""
    enabled = gate_enabled()
    if not enabled:
        return {"gate_enabled": False, "unlocked": True,
                "ttl_seconds": SESSION_TTL}
    sess = verify_session(token_from_cookie(cookies))
    if not sess:
        return {"gate_enabled": True, "unlocked": False,
                "ttl_seconds": SESSION_TTL}
    user = {}
    server = ""
    try:
        from cbhcli_pkg.auth import AuthStore
        store = AuthStore()
        user = store.get_user() or {}
        server = store.get_server()
    except Exception:
        pass
    return {"gate_enabled": True, "unlocked": True,
            "user": user, "server": server,
            "ttl_seconds": SESSION_TTL}


def unlock(key: str, ip: str = "") -> dict:
    """验证密钥并创建页面会话。

    Returns:
        成功: {"ok": True, "user": {...}, "token": "...", "ttl_seconds": N}
        失败: {"ok": False, "error": code, "message": "..."}
    """
    key = (key or "").strip()
    if not key:
        return {"ok": False, "error": "invalid_key", "message": "请输入认证密钥"}
    if rate_blocked(ip):
        return {"ok": False, "error": "rate_limited",
                "message": "验证尝试过于频繁，请稍后再试"}

    try:
        from cbhcli_pkg.auth import AuthStore, AuthClient, normalize_server, AuthError
    except Exception as e:  # pragma: no cover
        return {"ok": False, "error": "server_error", "message": str(e)}

    store = AuthStore()
    data = store.load()
    if not data:
        return {"ok": False, "error": "not_logged_in",
                "message": "本机尚未登录 cbhcli"}
    server = normalize_server(data.get("server", "") or "")
    if not server:
        return {"ok": False, "error": "unreachable",
                "message": "本机凭据缺少服务器地址"}
    expect_uid = str(((data.get("user") or {}).get("id", "")) or "")

    try:
        info = AuthClient(server=server).verify_key(server, key, surface="web")
    except AuthError as e:
        note_failure(ip)
        return {"ok": False, "error": e.code, "message": e.message}
    except Exception as e:
        note_failure(ip)
        return {"ok": False, "error": "server_error", "message": str(e)}

    user = info.get("user") or {}
    got_uid = str(user.get("id", "") or "")
    # 只允许本机当前登录用户的密钥（防止他人用自己的账号进入本机页面）
    if expect_uid and got_uid and got_uid != expect_uid:
        note_failure(ip)
        return {"ok": False, "error": "wrong_user",
                "message": "该密钥不属于本机当前登录的账号"}

    reset_rate(ip)
    token = create_session(got_uid or expect_uid)
    return {"ok": True, "user": user, "token": token,
            "ttl_seconds": SESSION_TTL}
