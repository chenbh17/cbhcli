"""面向客户端的认证 API（/api/*）

- POST /api/auth/login      登录（key -> token；限流 10 次/分钟/IP）
- POST /api/auth/validate   token 在线复核（剩余 <2 天滚动续期）
- POST /api/auth/heartbeat  60s 心跳（运行中会话集 + 权限快照）
- POST /api/auth/logout     吊销当前 token
- GET  /api/ping            连通性探测（无鉴权；客户端登录第一步）
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from cbhcli_pkg.admin import live
from cbhcli_pkg.admin.master import get_settings
from cbhcli_pkg.admin.store import get_store, now_iso

try:
    from cbhcli_pkg import __version__ as SERVER_VERSION
except Exception:  # pragma: no cover
    SERVER_VERSION = "0.0.0"

PROTOCOL = 1
RENEW_THRESHOLD_DAYS = 2.0      # 剩余 < 2 天自动滚动续期
LOGIN_RATE_LIMIT = 10           # 每 IP 每分钟登录尝试上限
LOGIN_RATE_WINDOW = 60.0

router = APIRouter()

# 登录限流：ip -> [timestamps]
_login_attempts: dict = {}


def _client_ip(request: Request) -> str:
    try:
        fwd = request.headers.get("x-forwarded-for", "")
        if fwd:
            return fwd.split(",")[0].strip()
        return request.client.host if request.client else ""
    except Exception:
        return ""


def _rate_limited(ip: str, limit: int, window: float) -> bool:
    now = time.time()
    bucket = [t for t in _login_attempts.get(ip, []) if now - t < window]
    if len(bucket) >= limit:
        _login_attempts[ip] = bucket
        return True
    bucket.append(now)
    _login_attempts[ip] = bucket
    return False


def _err(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse({"ok": False, "error": code, "message": message},
                        status_code=status)


def _bearer(request: Request) -> str:
    auth = request.headers.get("authorization", "") or ""
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return ""


def _client_info_str(client: Optional[dict]) -> str:
    try:
        return json.dumps(client or {}, ensure_ascii=False)[:500]
    except Exception:
        return ""


def _policy() -> dict:
    """客户端策略（login/validate/heartbeat 统一下发）。"""
    s = get_settings()
    return {
        "heartbeat_interval": 60,
        "report_level": s.get("report_level", "full"),
        "strict_online": bool(s.get("strict_online", False)),
        "strict_grace_minutes": s.get("strict_grace_minutes", 30),
        "snapshot_max_mb": s.get("snapshot_max_mb", 10),
        "auto_renew": bool(s.get("auto_renew", True)),
        "login_rate_limit": LOGIN_RATE_LIMIT,
    }


def _renew_check(token_row: dict, user: dict) -> dict:
    """滚动续期检查：剩余 < 2 天 -> 签发 new_token。"""
    out = {}
    exp = token_row.get("expires_at")
    out["token_expires_at"] = exp
    if not exp:
        return out                     # 永久 token
    try:
        dt = datetime.fromisoformat(str(exp))
        if dt.tzinfo is None:
            dt = dt.astimezone()
    except (ValueError, TypeError):
        return out
    remaining = (dt - datetime.now().astimezone()).total_seconds() / 86400.0
    if remaining >= RENEW_THRESHOLD_DAYS:
        return out
    if not _policy().get("auto_renew", True):
        return out
    store = get_store()
    ttl = user.get("token_ttl_days")
    new_token, new_exp = store.issue_token(
        user.get("id", ""), ttl if ttl is not None else 30,
        client_info=token_row.get("client_info", ""))
    # 旧 token 立即失效（一次续期只保留一条活跃 token 链）
    old_id = token_row.get("id", "")
    if old_id:
        try:
            store._exec("UPDATE tokens SET revoked=1 WHERE id=?", (old_id,))
        except Exception:
            pass
    out["new_token"] = new_token
    out["token_expires_at"] = new_exp
    return out


def _touch_sessions(user_id: str, running: list, instance: str = "") -> None:
    """把心跳里的运行中会话同步到内存注册表 + sessions 表。

    instance：上报进程实例标识（同一用户多进程各自维护自己的会话集合，
    心跳全量替换只影响本实例的条目，见 live.update_running 注释）。
    """
    store = get_store()
    live.update_running(user_id, running or [], instance=instance)
    for s in (running or []):
        if not isinstance(s, dict):
            continue
        sid = s.get("session_id", "")
        if not sid:
            continue
        status = (s.get("status") or "running").lower()
        store.upsert_session(
            user_id, sid,
            agent=s.get("agent", ""), workspace=s.get("workspace", ""),
            title=s.get("title", ""), model=s.get("model", ""),
            status=status,
            last_heartbeat=time.time(),
            updated_at=now_iso())


# ===================================================================
#  GET /api/ping
# ===================================================================

@router.get("/api/ping")
async def ping():
    return {"ok": True, "service": "cbhcli-admin",
            "version": SERVER_VERSION, "protocol": PROTOCOL,
            "server_time": time.time()}


# ===================================================================
#  POST /api/auth/login
# ===================================================================

@router.post("/api/auth/login")
async def login(request: Request):
    ip = _client_ip(request)
    if _rate_limited(ip, LOGIN_RATE_LIMIT, LOGIN_RATE_WINDOW):
        return _err(429, "rate_limited", "登录尝试过于频繁，请稍后再试")

    try:
        body = await request.json()
    except Exception:
        return _err(400, "bad_request", "请求体不是合法 JSON")
    if not isinstance(body, dict):
        return _err(400, "bad_request", "请求体格式错误")

    key = (body.get("key") or "").strip()
    client = body.get("client") or {}
    if not key:
        return _err(400, "invalid_key", "缺少认证密钥")
    proto = int(client.get("protocol", 0) or 0)
    if proto and proto != PROTOCOL:
        get_store().add_login("", False, ip=ip,
                              client_info=_client_info_str(client),
                              reason="protocol_mismatch")
        return _err(401, "protocol_mismatch",
                    f"协议版本不匹配（客户端 {proto}，服务端 {PROTOCOL}）")

    store = get_store()
    user = store.find_user_by_key(key)
    if not user:
        store.add_login("", False, ip=ip, client_info=_client_info_str(client),
                        reason="invalid_key")
        return _err(401, "invalid_key", "认证密钥无效（或已被重置）")
    if user.get("disabled"):
        store.add_login(user.get("id", ""), False, ip=ip,
                        client_info=_client_info_str(client),
                        reason="disabled")
        return _err(401, "disabled", "账号已被管理员禁用")

    # 用户有效期策略：None=永久（创建用户时已应用服务端默认值；
    # 此处不能再 fallback，否则"永久"会被降级为默认天数）
    ttl = user.get("token_ttl_days")
    token, expires_at = store.issue_token(
        user.get("id", ""), ttl, client_info=_client_info_str(client))

    store.touch_login(user.get("id", ""))
    store.touch_seen(user.get("id", ""))
    store.add_login(user.get("id", ""), True, ip=ip,
                    client_info=_client_info_str(client), reason="")
    live.touch_user(user.get("id", ""), client=client)
    try:
        live.set_user_surfaces(user.get("id", ""), [client.get("surface", "")])
    except Exception:
        pass

    return {
        "ok": True,
        "token": token,
        "expires_at": expires_at,
        "user": {"id": user.get("id", ""), "name": user.get("name", ""),
                 "display_name": user.get("display_name", "")},
        "key_prefix": user.get("key_prefix", ""),
        "server": {"version": SERVER_VERSION, "protocol": PROTOCOL},
        "policy": _policy(),
    }


# ===================================================================
#  POST /api/auth/validate
# ===================================================================

@router.post("/api/auth/validate")
async def validate(request: Request):
    token = _bearer(request)
    if not token:
        return _err(401, "not_logged_in", "缺少 Bearer token")
    store = get_store()
    result = store.verify_token(token)
    if not result:
        return _err(401, "invalid_key", "凭据已失效（过期/被吊销/被禁用）")

    user = result["user"]
    token_row = result["token"]
    store.touch_seen(user.get("id", ""))
    try:
        body = await request.json()
    except Exception:
        body = {}
    client = (body or {}).get("client") or {}
    if client:
        live.touch_user(user.get("id", ""), client=client)
        try:
            live.set_user_surfaces(user.get("id", ""), [client.get("surface", "")])
        except Exception:
            pass

    resp = {
        "ok": True,
        "user": user,
        "policy": _policy(),
    }
    resp.update(_renew_check(token_row, user))
    return resp


# ===================================================================
#  POST /api/auth/heartbeat
# ===================================================================

@router.post("/api/auth/heartbeat")
async def heartbeat(request: Request):
    token = _bearer(request)
    if not token:
        return _err(401, "not_logged_in", "缺少 Bearer token")
    store = get_store()
    result = store.verify_token(token)
    if not result:
        return _err(401, "invalid_key", "凭据已失效（过期/被吊销/被禁用）")

    user = result["user"]
    token_row = result["token"]
    try:
        body = await request.json()
    except Exception:
        body = {}
    body = body or {}
    client = body.get("client") or {}

    uid = user.get("id", "")
    running = body.get("running_sessions") or []
    perms = body.get("permissions") or {}
    instance = str(body.get("instance_id", "") or "")
    _touch_sessions(uid, running, instance=instance)
    live.touch_user(uid, client=client or None, permissions=perms or None)
    if client.get("surface"):
        try:
            surfaces = set((live.online_users.get(uid) or {}).get("surfaces") or [])
            surfaces.add(client.get("surface"))
            live.set_user_surfaces(uid, list(surfaces))
        except Exception:
            pass
    store.touch_seen(uid)

    resp = {
        "ok": True,
        "server_time": time.time(),
        "policy": _policy(),
    }
    resp.update(_renew_check(token_row, user))
    return resp


# ===================================================================
#  POST /api/auth/logout
# ===================================================================

@router.post("/api/auth/logout")
async def logout(request: Request):
    token = _bearer(request)
    store = get_store()
    if token:
        result = store.verify_token(token)
        store.revoke_token(token)
        if result:
            uid = (result.get("user") or {}).get("id", "")
            if uid:
                # 清理该用户在内存中的运行中会话
                for k in [k for k in live.running_sessions if k[0] == uid]:
                    live.running_sessions.pop(k, None)
    return {"ok": True}
