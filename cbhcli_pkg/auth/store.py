"""客户端凭据存储：~/.cbhcli/auth.json（0600）

设计要点（认证系统 v5.4.0）：
- 只存 token，不存原始认证密钥（密钥泄露面最小化）
- 文件权限 0600、目录 0700
- 路径支持环境变量 CBHCLI_AUTH_FILE 覆盖（测试/特殊部署）
- 提供 2s mtime 短缓存 is_logged_in_fast（Web 中间件每请求调用，避免高频 IO）
- 损坏/字段缺失视为未登录（引导重新 login / 界面内 /login）
"""
from __future__ import annotations

import json
import os
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

try:
    from cbhcli_pkg.config.global_config import CBHCLI_DIR
except Exception:  # pragma: no cover
    CBHCLI_DIR = Path(os.path.expanduser("~/.cbhcli"))

AUTH_FILE_ENV = "CBHCLI_AUTH_FILE"
_FILENAME = "auth.json"
_DEVICE_ID_FILENAME = "device_id"

# is_logged_in_fast 的 2s mtime 短缓存（Web 中间件高频调用）
_FAST_TTL = 2.0
_fast_lock = threading.Lock()
_fast_cache: dict = {"ts": 0.0, "mtime": None, "value": False}


def auth_file_path() -> Path:
    """凭据文件路径（CBHCLI_AUTH_FILE 优先；默认 ~/.cbhcli/auth.json）"""
    env = os.environ.get(AUTH_FILE_ENV, "").strip()
    if env:
        return Path(os.path.expanduser(env))
    return Path(str(CBHCLI_DIR)) / _FILENAME


def device_id_path() -> Path:
    """设备标识文件路径（独立于 auth.json，登出/重登保持不变）"""
    env = os.environ.get(AUTH_FILE_ENV, "").strip()
    if env:
        return Path(os.path.expanduser(env)).parent / _DEVICE_ID_FILENAME
    return Path(str(CBHCLI_DIR)) / _DEVICE_ID_FILENAME


def load_device_id() -> str:
    """读取设备标识；不存在则生成 uuid4 并持久化（失败返回临时 id）。"""
    p = device_id_path()
    try:
        if p.exists():
            did = p.read_text(encoding="utf-8").strip()
            if did:
                return did
    except Exception:
        pass
    did = "d_" + uuid.uuid4().hex[:16]
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(did, encoding="utf-8")
    except Exception:
        pass
    return did


def _chmod_quiet(path: Path, mode: int) -> None:
    try:
        os.chmod(str(path), mode)
    except Exception:
        pass


def parse_iso(ts: str) -> Optional[datetime]:
    """解析 ISO 时间串（容忍时区缺失，按本地时间理解），失败返回 None。"""
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is None:
        dt = dt.astimezone()  # 无时区 -> 视为本地时间
    return dt


def now_iso() -> str:
    return datetime.now().astimezone().isoformat()


class AuthStore:
    """auth.json 读写（线程安全；所有异常静默转为“未登录”语义）"""

    def __init__(self, path: Optional[Path] = None):
        self.path = Path(path) if path else auth_file_path()

    # ---------------------------------------------------------------
    #  读写
    # ---------------------------------------------------------------

    def load(self) -> Optional[dict]:
        """读取凭据；文件不存在/损坏/非 dict 返回 None。"""
        try:
            if not self.path.exists():
                return None
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                return None
            if not data.get("token") or not data.get("server"):
                return None
            return data
        except Exception:
            return None

    def save(self, data: dict) -> bool:
        """原子写入凭据（0600；先写临时文件再 os.replace）。"""
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            _chmod_quiet(self.path.parent, 0o700)
            tmp = self.path.with_suffix(".json.tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            _chmod_quiet(tmp, 0o600)
            os.replace(str(tmp), str(self.path))
            _chmod_quiet(self.path, 0o600)
            self._invalidate_fast()
            return True
        except Exception:
            return False

    def clear(self) -> bool:
        """删除本地凭据。"""
        try:
            if self.path.exists():
                self.path.unlink()
            self._invalidate_fast()
            return True
        except Exception:
            return False

    def _invalidate_fast(self) -> None:
        with _fast_lock:
            _fast_cache["ts"] = 0.0
            _fast_cache["mtime"] = None

    # ---------------------------------------------------------------
    #  状态查询
    # ---------------------------------------------------------------

    def is_logged_in(self, check_expiry: bool = False) -> bool:
        """是否已登录（check_expiry=True 时顺带判断本地有效期）。"""
        data = self.load()
        if not data:
            return False
        if check_expiry and self.is_expired(data):
            return False
        return True

    @staticmethod
    def is_expired(data: Optional[dict] = None) -> bool:
        """token 是否已过期（expires_at=null 表示永久，永不过期）。"""
        if data is None:
            return True
        exp = data.get("expires_at")
        if not exp:
            return False  # null/空 = 永久
        dt = parse_iso(exp)
        if dt is None:
            return False  # 无法解析视为未过期（避免误拦截）
        return datetime.now(timezone.utc) > dt

    @staticmethod
    def days_left(data: Optional[dict] = None) -> Optional[float]:
        """剩余天数；永久 token 返回 None。"""
        if not data:
            return None
        exp = data.get("expires_at")
        if not exp:
            return None
        dt = parse_iso(exp)
        if dt is None:
            return None
        return (dt - datetime.now(timezone.utc)).total_seconds() / 86400.0

    # ---------------------------------------------------------------
    #  便捷访问
    # ---------------------------------------------------------------

    def get_token(self) -> str:
        data = self.load() or {}
        return data.get("token", "") or ""

    def get_server(self) -> str:
        data = self.load() or {}
        return data.get("server", "") or ""

    def get_user(self) -> dict:
        data = self.load() or {}
        user = data.get("user")
        return user if isinstance(user, dict) else {}

    def touch_validated(self) -> None:
        """刷新 last_validated_at（validate 成功后调用）。"""
        data = self.load()
        if not data:
            return
        data["last_validated_at"] = now_iso()
        self.save(data)

    def needs_validate(self, max_age_seconds: float = 6 * 3600) -> bool:
        """距离上次在线校验是否超过阈值（默认 6 小时）。"""
        data = self.load()
        if not data:
            return True
        ts = data.get("last_validated_at") or data.get("logged_in_at") or ""
        dt = parse_iso(ts)
        if dt is None:
            return True
        return (datetime.now(timezone.utc) - dt).total_seconds() > max_age_seconds

    def update_after_login(self, server: str, resp: dict) -> bool:
        """登录成功后写入凭据（resp 为服务端 login 响应体）。"""
        user = resp.get("user") or {}
        data = {
            "version": 1,
            "server": server,
            "token": resp.get("token", ""),
            "expires_at": resp.get("expires_at"),   # null = 永久
            "user": {
                "id": user.get("id", ""),
                "name": user.get("name", ""),
                "display_name": user.get("display_name", ""),
            },
            "device_id": load_device_id(),
            "logged_in_at": now_iso(),
            "last_validated_at": now_iso(),
            "key_prefix": (resp.get("key_prefix") or ""),
            "policy": resp.get("policy") or {},
        }
        return self.save(data)

    def update_token(self, token: str, expires_at) -> bool:
        """滚动续期：透明替换 token 与有效期（保留其余字段）。"""
        data = self.load()
        if not data:
            return False
        data["token"] = token
        data["expires_at"] = expires_at
        data["last_validated_at"] = now_iso()
        return self.save(data)

    def update_expiry(self, expires_at) -> bool:
        """仅更新有效期（管理员续期 + 原 token 仍有效时的无感恢复）。"""
        data = self.load()
        if not data:
            return False
        data["expires_at"] = expires_at
        return self.save(data)


def default_store() -> AuthStore:
    return AuthStore()


# ===================================================================
#  模块级快捷函数（供 Web 中间件等高频繁调用场景）
# ===================================================================

def is_logged_in_fast() -> bool:
    """本机是否已登录（本地视角，含 2s mtime 短缓存）。

    只判断“有凭据且本地未过期”，不做在线校验——Web 中间件用；
    与 guard.require_login 的在线复核语义不同。
    """
    store = AuthStore()
    p = store.path
    now = time.time()
    with _fast_lock:
        if now - _fast_cache["ts"] < _FAST_TTL:
            return bool(_fast_cache["value"])
    try:
        mtime = p.stat().st_mtime_ns if p.exists() else -1
    except Exception:
        mtime = -1
    with _fast_lock:
        if _fast_cache["mtime"] == mtime and now - _fast_cache["ts"] < _FAST_TTL:
            return bool(_fast_cache["value"])
    value = store.is_logged_in(check_expiry=True)
    with _fast_lock:
        _fast_cache["ts"] = now
        _fast_cache["mtime"] = mtime
        _fast_cache["value"] = value
    return value


def auth_status() -> dict:
    """本机登录状态摘要（Web /api/auth/status 用）。"""
    store = AuthStore()
    data = store.load()
    if not data:
        return {"logged_in": False}
    expired = store.is_expired(data)
    return {
        "logged_in": not expired,
        "expired": expired,
        "server": data.get("server", ""),
        "user": data.get("user") or {},
        "expires_at": data.get("expires_at"),
        "key_prefix": data.get("key_prefix", ""),
        "device_id": data.get("device_id", ""),
        "logged_in_at": data.get("logged_in_at", ""),
        "last_validated_at": data.get("last_validated_at", ""),
        "policy": data.get("policy") or {},
    }
