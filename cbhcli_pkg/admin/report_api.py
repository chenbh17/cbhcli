"""审计上报接收（/api/report/*）

- POST /api/report/batch     事件流（≤100 条/批；eid 幂等去重）
  · type=session_status 的轻量事件即时更新实时注册表（"运行中"秒级上屏）
- POST /api/report/snapshot  会话快照（低频重型；updated_at+message_count 去重；
                             超 snapshot_max_mb 返回 413）
"""
from __future__ import annotations

import json
import time

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from cbhcli_pkg.admin import live
from cbhcli_pkg.admin.master import get_settings
from cbhcli_pkg.admin.store import get_store, now_iso

BATCH_MAX = 100
EVENT_PAYLOAD_MAX = 32 * 1024

router = APIRouter()


def _err(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse({"ok": False, "error": code, "message": message},
                        status_code=status)


def _bearer(request: Request) -> str:
    auth = request.headers.get("authorization", "") or ""
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return ""


def _verify(request: Request):
    """返回 (user, token_row) 或 None（调用方回 401）"""
    token = _bearer(request)
    if not token:
        return None
    result = get_store().verify_token(token)
    if not result:
        return None
    return result["user"], result["token"]


def _apply_status_event(user_id: str, ev: dict) -> None:
    """session_status 轻量事件：即时更新实时注册表 + sessions 表。"""
    payload = ev.get("payload") or {}
    sid = ev.get("session_id", "") or payload.get("session_id", "")
    if not sid:
        return
    status = (payload.get("status") or "running").lower()
    # 只收非空字段（避免空值覆盖已有信息——CLI 轮结束的 idle 事件不带
    # title/workspace，此前会把会话标题/工作空间清空）
    info = {}
    for k in ("agent", "title", "workspace", "surface"):
        v = payload.get(k, "")
        if v:
            info[k] = (str(v)[:200] if k == "title" else v)
    # v5.4.0：进程实例透传（多进程运行集合隔离，见 live.update_running）
    instance = str(payload.get("instance_id", "") or "")
    live.set_session_status(user_id, sid, status, info, instance=instance)
    store = get_store()
    fields = {
        "status": status,
        "last_heartbeat": time.time() if status == "running" else None,
        "updated_at": now_iso(),
    }
    for k in ("agent", "title", "workspace"):
        if info.get(k):
            fields[k] = info[k]
    store.upsert_session(user_id, sid, **fields)


@router.post("/api/report/batch")
async def report_batch(request: Request):
    verified = _verify(request)
    if not verified:
        return _err(401, "invalid_key", "凭据已失效")
    user, _token_row = verified
    uid = user.get("id", "")

    try:
        body = await request.json()
    except Exception:
        return _err(400, "bad_request", "请求体不是合法 JSON")
    events = (body or {}).get("events") or []
    if not isinstance(events, list):
        return _err(400, "bad_request", "events 必须是数组")
    if len(events) > BATCH_MAX:
        events = events[:BATCH_MAX]

    clean = []
    for ev in events:
        if not isinstance(ev, dict):
            continue
        try:
            size = len(json.dumps(ev.get("payload") or {},
                                  ensure_ascii=False).encode("utf-8"))
        except Exception:
            size = 0
        if size > EVENT_PAYLOAD_MAX:
            continue
        clean.append(ev)

    accepted_out: list = []
    accepted = get_store().insert_events(uid, clean, accepted_out=accepted_out)
    # 实时状态事件即时生效（"运行中"秒级上屏）
    for ev in clean:
        if str(ev.get("type", "")) == "session_status":
            try:
                _apply_status_event(uid, ev)
            except Exception:
                pass
    # v5.4.0：实时转发给管理页面（WebSocket 实时画面；无连接时零开销）
    try:
        from cbhcli_pkg.admin import ws as ws_hub
        await ws_hub.broadcast_events(uid, accepted_out)
    except Exception:
        pass

    return {"ok": True, "accepted": accepted}


@router.post("/api/report/snapshot")
async def report_snapshot(request: Request):
    verified = _verify(request)
    if not verified:
        return _err(401, "invalid_key", "凭据已失效")
    user, _token_row = verified
    uid = user.get("id", "")

    try:
        body = await request.json()
    except Exception:
        return _err(400, "bad_request", "请求体不是合法 JSON")
    body = body or {}
    sid = (body.get("session_id") or "").strip()
    data = body.get("data")
    if not sid or not isinstance(data, dict):
        return _err(400, "bad_request", "缺少 session_id 或 data")

    # 大小限制（policy.snapshot_max_mb）
    try:
        max_mb = float(get_settings().get("snapshot_max_mb", 10) or 10)
    except Exception:
        max_mb = 10.0
    if not (max_mb > 0):
        max_mb = 10.0
    try:
        size = len(json.dumps(data, ensure_ascii=False).encode("utf-8"))
    except Exception:
        size = 0
    if size > max_mb * 1024 * 1024:
        return _err(413, "too_large",
                    f"快照超过上限 {max_mb}MB（当前 {size / 1048576:.1f}MB）")

    store = get_store()
    updated_at = str(body.get("updated_at", "") or "")[:64]
    message_count = int(body.get("message_count", 0) or 0)

    # 去重：与已存会话记录比对（updated_at + message_count 未变化则跳过）
    existing = store.get_session(uid, sid)
    if existing and (existing.get("updated_at") or "") == updated_at \
            and int(existing.get("message_count", 0) or 0) == message_count:
        return {"ok": True, "skipped": True}

    if not store.save_snapshot(uid, sid, data):
        return _err(500, "server_error", "快照写入失败")

    store.upsert_session(
        uid, sid,
        agent=body.get("agent", "") or "",
        workspace=str(data.get("workspace", "") or ""),
        title=str(data.get("title", "") or "")[:200],
        updated_at=updated_at or now_iso(),
        message_count=message_count,
        created_at=str(data.get("created_at", "") or ""),
    )
    return {"ok": True}
