"""管理页面 API（/admin/api/*，需管理员会话）

管理员会话：管理页面登录（POST /admin/api/login {password}）-> admin_token
（内存存储，24h 过期）；以下 API 校验 X-Admin-Token 头。
"""
from __future__ import annotations

import csv
import io
import json
import os
import re
import secrets
import shutil
import time
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, PlainTextResponse

from cbhcli_pkg.admin import data_dir, live
from cbhcli_pkg.admin.master import (get_settings, master_key_source,
                                     set_new_master_key, sync_process_env,
                                     update_settings, validate_settings_patch,
                                     verify_master_key)
from cbhcli_pkg.admin.store import _normalize_user_ttl, get_store, now_iso

try:
    from cbhcli_pkg import __version__ as SERVER_VERSION
except Exception:  # pragma: no cover
    SERVER_VERSION = "0.0.0"

ADMIN_TOKEN_TTL = 24 * 3600
ADMIN_LOGIN_LIMIT = 5           # 每 IP 每分钟
ADMIN_LOGIN_WINDOW = 60.0

# admin_token -> {"ts": float, "ip": str}
_admin_tokens: dict = {}
_admin_login_attempts: dict = {}
_server_start_ts = time.time()

router_open = APIRouter(prefix="/admin/api")     # 无需管理员会话（登录）
router = APIRouter(prefix="/admin/api")          # 需管理员会话


# ===================================================================
#  管理员会话
# ===================================================================

def _client_ip(request: Request) -> str:
    try:
        fwd = request.headers.get("x-forwarded-for", "")
        if fwd:
            return fwd.split(",")[0].strip()
        return request.client.host if request.client else ""
    except Exception:
        return ""


def _prune_admin_tokens() -> None:
    now = time.time()
    for t in [t for t, v in _admin_tokens.items()
              if now - float(v.get("ts", 0)) > ADMIN_TOKEN_TTL]:
        _admin_tokens.pop(t, None)


def check_admin_token(token: str) -> bool:
    """校验 admin_token 字符串（HTTP 头 / WebSocket query 共用）。"""
    tok = (token or "").strip()
    if not tok:
        return False
    _prune_admin_tokens()
    info = _admin_tokens.get(tok)
    if not info:
        return False
    if time.time() - float(info.get("ts", 0)) > ADMIN_TOKEN_TTL:
        _admin_tokens.pop(tok, None)
        return False
    return True


def _check_admin(request: Request) -> bool:
    return check_admin_token(request.headers.get("x-admin-token", "") or "")


async def _require_admin(request: Request):
    """依赖占位（当前用手动 _guard；保留供未来使用）。"""
    if not _check_admin(request):
        return JSONResponse({"ok": False, "error": "unauthorized",
                             "message": "管理员会话无效或已过期，请重新登录"},
                            status_code=401)


def _guard(request: Request):
    """在 handler 内手动调用：未通过返回 JSONResponse，通过返回 None。"""
    if not _check_admin(request):
        return JSONResponse({"ok": False, "error": "unauthorized",
                             "message": "管理员会话无效或已过期，请重新登录"},
                            status_code=401)
    return None


def _log_admin_action(action: str, detail: dict = None) -> None:
    """记录管理操作到事件表（user_id='__admin__'，审计用）。"""
    try:
        get_store().insert_events("__admin__", [{
            "eid": "e_admin_" + secrets.token_hex(8),
            "ts": time.time(),
            "type": f"admin_action.{action}",
            "session_id": "",
            "payload": detail or {},
        }])
    except Exception:
        pass


# ===================================================================
#  登录（无管理员会话门）
# ===================================================================

@router_open.post("/login")
async def admin_login(request: Request):
    ip = _client_ip(request)
    now = time.time()
    # 限流只统计「失败」尝试（成功登录清空计数，避免改完密码重新登录被误伤）
    bucket = [t for t in _admin_login_attempts.get(ip, [])
              if now - t < ADMIN_LOGIN_WINDOW]
    if len(bucket) >= ADMIN_LOGIN_LIMIT:
        _admin_login_attempts[ip] = bucket
        return JSONResponse(
            {"ok": False, "error": "rate_limited",
             "message": "登录失败次数过多，请 1 分钟后再试"}, status_code=429)

    try:
        body = await request.json()
    except Exception:
        body = {}
    password = str((body or {}).get("password", ""))
    if not verify_master_key(password):
        bucket.append(now)
        _admin_login_attempts[ip] = bucket
        return JSONResponse(
            {"ok": False, "error": "invalid_password", "message": "主密码错误"},
            status_code=401)
    _admin_login_attempts.pop(ip, None)

    tok = "adm_" + secrets.token_urlsafe(32)
    _admin_tokens[tok] = {"ts": time.time(), "ip": ip}
    _log_admin_action("login", {"ip": ip})
    return {"ok": True, "admin_token": tok, "expires_in": ADMIN_TOKEN_TTL,
            "version": SERVER_VERSION}


@router_open.post("/logout")
async def admin_logout(request: Request):
    tok = request.headers.get("x-admin-token", "") or ""
    _admin_tokens.pop(tok, None)
    return {"ok": True}


# ===================================================================
#  仪表盘 / 健康 / 实时
# ===================================================================

@router.get("/overview")
async def overview(request: Request):
    if (r := _guard(request)):
        return r
    store = get_store()
    ov = store.overview()
    snap = live.live_snapshot()
    return {
        "ok": True,
        "stats": {
            "users": ov["users"],
            "online": snap["online_count"],
            "today_sessions": ov["today_sessions"],
            "running": snap["running_count"],
            "today_events": ov["today_events"],
            "total_sessions": ov["total_sessions"],
            "total_events": ov["total_events"],
        },
        "recent_events": _events_brief(store.recent_events(limit=30)),
        "online_users": snap["online_users"],
        "server_time": time.time(),
    }


# 完整转义序列（CSI/OSC/charset 等）；json.dumps 后 ESC 会变为字面量反斜杠u001b，
# 故两种形式都要匹配
_ANSI_RE = re.compile(
    r"(?:\x1b|\\u001[bB])"
    r"(?:\[[0-9;?]*[A-Za-z]"
    r"|\][^\x07\x1b]*(?:\x07|\x1b\\)"
    r"|\([0-9A-B]"
    r"|[=>#][0-9]?)")
_ANSI_BARE_RE = re.compile(r"\x1b|\\u001[bB]")


def _strip_ansi(s) -> str:
    """清除终端颜色/控制转义（v5.4.2：CSV 导出等纯文本场景去乱码）。"""
    try:
        s = _ANSI_RE.sub("", str(s or ""))
        return _ANSI_BARE_RE.sub("", s)   # 兜底：不成序列的孤立 ESC
    except Exception:
        return str(s or "")


def _events_brief(rows: list) -> list:
    out = []
    for r in rows:
        out.append({
            "id": r.get("id"),
            "ts": r.get("recv_ts"),
            "client_ts": r.get("ts"),
            "user_id": r.get("user_id", ""),
            "user_name": r.get("user_name") or r.get("user_id", ""),
            "session_id": r.get("session_id", ""),
            "type": r.get("type", ""),
            "payload": r.get("payload_obj") or {},
        })
    return out


@router.get("/health")
async def health(request: Request):
    if (r := _guard(request)):
        return r
    store = get_store()
    d = data_dir()
    db_ok = True
    try:
        store._query_one("SELECT 1 AS x")
    except Exception:
        db_ok = False
    try:
        usage = shutil.disk_usage(str(d))
        disk_free = f"{usage.free / (1024 ** 3):.1f} GB"
        disk_total = f"{usage.total / (1024 ** 3):.1f} GB"
    except Exception:
        disk_free, disk_total = "?", "?"
    ov = store.overview()
    return {
        "ok": True,
        "uptime_seconds": time.time() - _server_start_ts,
        "started_at": datetime.fromtimestamp(_server_start_ts).astimezone().isoformat(
            timespec="seconds"),
        "version": SERVER_VERSION,
        "protocol": 1,
        "db_ok": db_ok,
        "db_size": store.db_size_bytes(),
        "snapshot_count": store.snapshot_count(),
        "data_dir": str(d),
        "disk_free": disk_free,
        "disk_total": disk_total,
        "last_event_ts": ov.get("last_event_ts", 0),
        "master_key_source": master_key_source(),
        "server_time": time.time(),
    }


@router.get("/live")
async def live_view(request: Request):
    if (r := _guard(request)):
        return r
    snap = live.live_snapshot()
    # 补充用户名
    store = get_store()
    users = {u["id"]: u for u in store.list_users()}
    for item in snap["online_users"]:
        u = users.get(item.get("user_id", ""), {})
        item["name"] = u.get("name", "")
        item["display_name"] = u.get("display_name", "")
    for item in snap["running_sessions"]:
        u = users.get(item.get("user_id", ""), {})
        item["name"] = u.get("name", "")
        item["display_name"] = u.get("display_name", "")
    snap["ok"] = True
    return snap


# ===================================================================
#  用户管理
# ===================================================================

@router.get("/users")
async def list_users(request: Request):
    if (r := _guard(request)):
        return r
    store = get_store()
    users = store.list_users()
    snap = live.live_snapshot()
    online_ids = {u["user_id"] for u in snap["online_users"]}
    running_count: dict = {}
    for s in snap["running_sessions"]:
        running_count[s["user_id"]] = running_count.get(s["user_id"], 0) + 1
    for u in users:
        u["online"] = u.get("id") in online_ids
        u["running_sessions"] = running_count.get(u.get("id"), 0)
    return {"ok": True, "users": users}


@router.post("/users")
async def create_user(request: Request):
    if (r := _guard(request)):
        return r
    try:
        body = await request.json()
    except Exception:
        body = {}
    body = body or {}
    name = str(body.get("name", "")).strip()
    if not name:
        return JSONResponse({"ok": False, "error": "bad_request",
                             "message": "用户名不能为空"}, status_code=400)
    ttl = body.get("token_ttl_days", get_settings().get(
        "default_token_ttl_days", 30))
    try:
        user, key = get_store().create_user(
            name, display_name=str(body.get("display_name", "") or ""),
            note=str(body.get("note", "") or ""), token_ttl_days=ttl)
    except ValueError as e:
        return JSONResponse({"ok": False, "error": "duplicate",
                             "message": str(e)}, status_code=400)
    _log_admin_action("create_user", {"name": name, "ttl": ttl})
    return {"ok": True, "user": user, "key": key,
            "notice": "密钥已保存，可随时在用户列表点击「眼睛」查看并复制"}


@router.put("/users/{user_id}")
async def update_user(user_id: str, request: Request):
    if (r := _guard(request)):
        return r
    try:
        body = await request.json()
    except Exception:
        body = {}
    body = body or {}
    allowed = {k: v for k, v in body.items()
               if k in ("display_name", "note", "disabled", "token_ttl_days",
                        "name")}
    try:
        user = get_store().update_user(user_id, **allowed)
    except ValueError as e:
        return JSONResponse({"ok": False, "error": "duplicate",
                             "message": str(e)}, status_code=400)
    if not user:
        return JSONResponse({"ok": False, "error": "not_found",
                             "message": "用户不存在"}, status_code=404)
    _log_admin_action("update_user", {"user_id": user_id, "fields": allowed})
    # 禁用后立即清理在线/运行态
    if allowed.get("disabled"):
        live.online_users.pop(user_id, None)
        for k in [k for k in live.running_sessions if k[0] == user_id]:
            live.running_sessions.pop(k, None)
    return {"ok": True, "user": user}


@router.delete("/users/{user_id}")
async def delete_user(user_id: str, request: Request, purge: int = 0):
    if (r := _guard(request)):
        return r
    ok = get_store().delete_user(user_id, purge=bool(purge))
    if not ok:
        return JSONResponse({"ok": False, "error": "not_found",
                             "message": "用户不存在"}, status_code=404)
    live.online_users.pop(user_id, None)
    for k in [k for k in live.running_sessions if k[0] == user_id]:
        live.running_sessions.pop(k, None)
    _log_admin_action("delete_user", {"user_id": user_id, "purge": bool(purge)})
    return {"ok": True}


@router.post("/users/{user_id}/reset_key")
async def reset_key(user_id: str, request: Request):
    if (r := _guard(request)):
        return r
    user, key = get_store().reset_key(user_id)
    if not user:
        return JSONResponse({"ok": False, "error": "not_found",
                             "message": "用户不存在"}, status_code=404)
    _log_admin_action("reset_key", {"user_id": user_id})
    return {"ok": True, "user": user, "key": key,
            "notice": "旧密钥与全部旧凭据已失效；新密钥可随时在用户列表查看"}


@router.post("/users/{user_id}/renew")
async def renew_user(user_id: str, request: Request):
    if (r := _guard(request)):
        return r
    try:
        body = await request.json()
    except Exception:
        body = {}
    body = body or {}
    ttl = body.get("ttl_days", 30)
    ok, ttl_norm = _normalize_user_ttl(ttl)
    if not ok:
        return JSONResponse({"ok": False, "error": "bad_request",
                             "message": "无效的有效期（天数 / forever）"},
                            status_code=400)
    user, updated, expires_at = get_store().renew_user(user_id, ttl_norm)
    if not user:
        return JSONResponse({"ok": False, "error": "not_found",
                             "message": "用户不存在"}, status_code=404)
    _log_admin_action("renew", {"user_id": user_id, "ttl": ttl,
                                "tokens_updated": updated})
    return {"ok": True, "user": user, "tokens_updated": updated,
            "expires_at": expires_at}


@router.get("/users/{user_id}/permissions")
async def user_permissions(user_id: str, request: Request):
    if (r := _guard(request)):
        return r
    perms = live.user_permissions(user_id)
    snap = live.live_snapshot()
    online = [u for u in snap["online_users"] if u.get("user_id") == user_id]
    running = [s for s in snap["running_sessions"] if s.get("user_id") == user_id]
    return {"ok": True, "permissions": perms,
            "online": bool(online), "running_sessions": running}


@router.get("/users/{user_id}/tokens")
async def user_tokens(user_id: str, request: Request):
    if (r := _guard(request)):
        return r
    return {"ok": True, "tokens": get_store().list_user_tokens(user_id)}


@router.get("/users/{user_id}/key")
async def user_key(user_id: str, request: Request):
    """查看用户密钥（管理页面"眼睛"按钮）。

    - key：完整明文密钥（升级前创建的用户为空）
    - prefix：可公开的前缀（cbhk_xxxxxxxx）
    - available：是否存在明文（False 时前端提示重置密钥）
    """
    if (r := _guard(request)):
        return r
    user = get_store().get_user(user_id)
    if not user:
        return JSONResponse({"ok": False, "error": "not_found",
                             "message": "用户不存在"}, status_code=404)
    info = get_store().get_user_key(user_id)
    _log_admin_action("view_key", {"user_id": user_id,
                                   "available": info.get("available", False)})
    return {"ok": True, **info}


# ===================================================================
#  会话管理
# ===================================================================

@router.get("/sessions")
async def list_sessions(request: Request, user_id: str = "", status: str = "",
                        q: str = "", from_ts: float = 0, to_ts: float = 0,
                        limit: int = 100, offset: int = 0):
    if (r := _guard(request)):
        return r
    rows, total = get_store().list_sessions(
        user_id=user_id, status=status, q=q, from_ts=from_ts, to_ts=to_ts,
        limit=limit, offset=offset)
    # 运行中以内存注册表为准（数据库保留最近状态）
    snap = live.live_snapshot()
    live_ids = {s["session_id"] for s in snap["running_sessions"]}
    for r_ in rows:
        r_["status"] = "running" if r_.get("session_id") in live_ids \
            else r_.get("status", "idle")
    return {"ok": True, "sessions": rows, "total": total}


@router.get("/sessions/{user_id}/{session_id}")
async def get_session(user_id: str, session_id: str, request: Request):
    if (r := _guard(request)):
        return r
    store = get_store()
    meta = store.get_session(user_id, session_id) or {}
    data = store.load_snapshot(user_id, session_id)
    snap = live.live_snapshot()
    running = [s for s in snap["running_sessions"]
               if s.get("session_id") == session_id
               and s.get("user_id") == user_id]
    return {
        "ok": True,
        "meta": meta,
        "running": bool(running),
        "data": data,
    }


@router.delete("/sessions/{user_id}/{session_id}")
async def delete_session(user_id: str, session_id: str, request: Request):
    if (r := _guard(request)):
        return r
    get_store().delete_session(user_id, session_id)
    live.drop_session(user_id, session_id)
    _log_admin_action("delete_session", {"user_id": user_id,
                                         "session_id": session_id})
    return {"ok": True}


# ===================================================================
#  事件 / 登录记录 / 导出
# ===================================================================

@router.get("/events")
async def list_events(request: Request, type: str = "", user_id: str = "",
                      session_id: str = "", from_ts: float = 0,
                      to_ts: float = 0, limit: int = 100, offset: int = 0,
                      no_session: int = 0):
    if (r := _guard(request)):
        return r
    rows, total = get_store().list_events(
        type_=type, user_id=user_id, session_id=session_id, from_ts=from_ts,
        to_ts=to_ts, limit=limit, offset=offset, no_session=bool(no_session))
    return {"ok": True, "events": _events_brief(rows), "total": total}


@router.get("/events/summary_users")
async def events_summary_users(request: Request, from_ts: float = 0,
                               to_ts: float = 0,
                               limit: int = 500, offset: int = 0):
    """审计分层·用户级（v5.4.2）：按用户聚合事件统计。"""
    if (r := _guard(request)):
        return r
    rows, total = get_store().events_summary_by_user(
        from_ts=from_ts, to_ts=to_ts, limit=limit, offset=offset)
    snap = live.live_snapshot()
    online_ids = {u.get("user_id") for u in snap.get("online_users", [])
                  if u.get("online")}
    running_ids = {s.get("user_id") for s in snap.get("running_sessions", [])}
    for r_ in rows:
        uid = r_.get("user_id", "")
        r_["online"] = uid in online_ids
        r_["running"] = uid in running_ids
    return {"ok": True, "users": rows, "total": total}


@router.get("/events/summary_sessions")
async def events_summary_sessions(request: Request, user_id: str = "",
                                  from_ts: float = 0, to_ts: float = 0,
                                  limit: int = 100, offset: int = 0):
    """审计分层·会话级（v5.4.2）：按会话聚合事件统计（含会话元数据）。"""
    if (r := _guard(request)):
        return r
    rows, total = get_store().events_summary_by_session(
        user_id=user_id, from_ts=from_ts, to_ts=to_ts,
        limit=limit, offset=offset)
    snap = live.live_snapshot()
    live_ids = {s.get("session_id") for s in snap.get("running_sessions", [])}
    for r_ in rows:
        sid = r_.get("session_id", "")
        r_["status"] = "running" if sid and sid in live_ids \
            else (r_.get("status") or "idle")
    return {"ok": True, "sessions": rows, "total": total}


@router.get("/event_types")
async def event_types(request: Request):
    if (r := _guard(request)):
        return r
    rows = get_store()._query(
        "SELECT type, COUNT(*) AS c FROM events GROUP BY type"
        " ORDER BY c DESC LIMIT 60")
    return {"ok": True, "types": rows}


@router.get("/logins")
async def list_logins(request: Request, user_id: str = "",
                      success: Optional[int] = None,
                      limit: int = 100, offset: int = 0):
    if (r := _guard(request)):
        return r
    suc = None if success is None else bool(success)
    rows, total = get_store().list_logins(
        user_id=user_id, success=suc, limit=limit, offset=offset)
    return {"ok": True, "logins": rows, "total": total}


@router.get("/export/events.csv")
async def export_events(request: Request, type: str = "", user_id: str = "",
                        session_id: str = "", from_ts: float = 0,
                        to_ts: float = 0, no_session: int = 0):
    if (r := _guard(request)):
        return r
    rows = get_store().events_for_export(
        type_=type, user_id=user_id, session_id=session_id,
        from_ts=from_ts, to_ts=to_ts, no_session=bool(no_session))
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["时间(服务端)", "时间(客户端)", "用户", "会话", "类型", "摘要"])
    for r_ in rows:
        w.writerow([
            _iso(r_.get("recv_ts")), _iso(r_.get("ts")),
            r_.get("user_name") or r_.get("user_id", ""),
            r_.get("session_id", ""), r_.get("type", ""),
            _strip_ansi(json.dumps(r_.get("payload_obj") or {}, ensure_ascii=False))[:500],
        ])
    return _csv_response(buf.getvalue(), "cbhcli_events.csv")


@router.get("/export/sessions.csv")
async def export_sessions(request: Request, user_id: str = "", status: str = "",
                          q: str = ""):
    if (r := _guard(request)):
        return r
    rows, _ = get_store().list_sessions(
        user_id=user_id, status=status, q=q, limit=100000, offset=0)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["用户", "会话ID", "Agent", "标题", "模型", "工作空间",
                "消息数", "状态", "最后活跃"])
    for r_ in rows:
        w.writerow([
            r_.get("user_name") or r_.get("user_id", ""),
            r_.get("session_id", ""), r_.get("agent", ""), r_.get("title", ""),
            r_.get("model", ""), r_.get("workspace", ""),
            r_.get("message_count", 0), r_.get("status", ""),
            _iso(r_.get("last_heartbeat")),
        ])
    return _csv_response(buf.getvalue(), "cbhcli_sessions.csv")


def _iso(ts) -> str:
    try:
        ts = float(ts or 0)
        if ts <= 0:
            return ""
        return datetime.fromtimestamp(ts).astimezone().isoformat(
            timespec="seconds")
    except Exception:
        return ""


def _csv_response(text: str, filename: str) -> PlainTextResponse:
    # ﻿ BOM 让 Excel 正确识别 UTF-8
    return PlainTextResponse(
        "﻿" + text, media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'})


# ===================================================================
#  设置
# ===================================================================

@router.get("/settings")
async def get_settings_api(request: Request):
    if (r := _guard(request)):
        return r
    return {"ok": True, "settings": get_settings(),
            "data_dir": str(data_dir()),
            "version": SERVER_VERSION,
            "master_key_source": master_key_source()}


@router.put("/settings")
async def put_settings(request: Request):
    if (r := _guard(request)):
        return r
    try:
        body = await request.json()
    except Exception:
        body = {}
    ok, err = validate_settings_patch(body or {})
    if not ok:
        return JSONResponse({"ok": False, "error": "bad_request",
                             "message": err}, status_code=400)
    settings = update_settings(body or {})
    _log_admin_action("update_settings", {"patch": body or {}})
    return {"ok": True, "settings": settings}


class _PasswordBody(dict):
    pass


@router.post("/settings/password")
async def change_password(request: Request):
    if (r := _guard(request)):
        return r
    try:
        body = await request.json()
    except Exception:
        body = {}
    body = body or {}
    old = str(body.get("old_password", ""))
    new = str(body.get("new_password", "")).strip()
    if not verify_master_key(old):
        return JSONResponse({"ok": False, "error": "invalid_password",
                             "message": "当前主密码错误"}, status_code=401)
    if len(new) < 4:
        return JSONResponse({"ok": False, "error": "bad_request",
                             "message": "新密码过短（至少 4 位）"}, status_code=400)
    env_active = bool(os.environ.get("CBHCLI_ADMIN_MASTER_KEY", "").strip())
    if not set_new_master_key(new):
        return JSONResponse({"ok": False, "error": "server_error",
                             "message": "写入失败"}, status_code=500)
    # 进程内环境变量自动同步为新密码（启动配置无需人工修改）
    sync_process_env(new)
    # 修改主密码后：其它管理页面会话立即失效（保留当前会话）
    cur = request.headers.get("x-admin-token", "") or ""
    revoked = 0
    for t in [t for t in list(_admin_tokens) if t != cur]:
        _admin_tokens.pop(t, None)
        revoked += 1
    _log_admin_action("change_password", {"env_override": env_active,
                                          "revoked_others": revoked})
    resp = {
        "ok": True,
        "message": "主密码已更新，立即生效（可用新密码登录管理页面）",
        "revoked_others": revoked,
    }
    if env_active:
        resp["warning"] = (
            "检测到启动配置中存在环境变量 CBHCLI_ADMIN_MASTER_KEY："
            "新主密码已优先生效，环境变量不再作为登录凭据"
            "（服务已自动同步，启动配置无需修改）。")
    return resp
