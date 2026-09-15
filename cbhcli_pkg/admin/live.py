"""内存实时状态注册表（online_users / running_sessions）

- 心跳/上报时更新；`GET /admin/api/live` 聚合输出（清理超 TTL 条目）
- 在线判定：last_seen 在 150s 内（心跳 60s，2.5 个周期容错）
- 运行中判定：last_beat 在 180s 内
- 服务重启后内存清空（由下一次心跳自然恢复；数据库 sessions.status 保留
  最近状态供列表展示）
"""
from __future__ import annotations

import threading
import time
from typing import Optional

ONLINE_TTL = 150.0        # 在线判定 TTL（秒）
RUNNING_TTL = 240.0       # 运行中判定 TTL（秒；4 个心跳周期缓冲）

_lock = threading.RLock()
online_users: dict = {}      # user_id -> {last_seen, client, permissions, surfaces}
running_sessions: dict = {}  # (user_id, session_id) -> {...}


def touch_user(user_id: str, client: Optional[dict] = None,
               permissions: Optional[dict] = None) -> None:
    """记录用户心跳（在线 + 权限快照）。"""
    if not user_id:
        return
    with _lock:
        cur = online_users.get(user_id) or {}
        cur["last_seen"] = time.time()
        if client:
            cur["client"] = dict(client)
        if permissions:
            cur["permissions"] = dict(permissions)
        online_users[user_id] = cur


def set_user_surfaces(user_id: str, surfaces) -> None:
    with _lock:
        cur = online_users.get(user_id) or {}
        cur["surfaces"] = sorted(set(surfaces or []))
        cur["last_seen"] = time.time()
        online_users[user_id] = cur


def update_running(user_id: str, sessions: list, instance: str = "",
                   replace: bool = True) -> None:
    """同步该用户的运行中会话集合（心跳全量上报，replace=True）。

    v5.4.0 修复：按上报进程实例（instance）隔离——心跳的全量替换只影响
    本实例拥有的条目。此前多进程（如常驻 Web 服务进程的空闲心跳 vs CLI
    正在运行的会话）互相 replace 删除同一用户的 running_sessions，导致
    "运行中会话在看板里闪烁/消失"。

    instance 缺省（旧客户端）归入 "legacy" 槽位。
    replace=False 时按条目合并（batch 上报的场景）。
    """
    if not user_id:
        return
    inst = instance or "legacy"
    now = time.time()
    with _lock:
        owned = set()
        if replace:
            for k, v in list(running_sessions.items()):
                if k[0] == user_id and (v.get("instance") or "legacy") == inst:
                    owned.add(k)
        new_keys = set()
        for s in sessions or []:
            if not isinstance(s, dict):
                continue
            sid = s.get("session_id", "")
            if not sid:
                continue
            status = (s.get("status") or "running").lower()
            key = (user_id, sid)
            if status != "running":
                cur = running_sessions.get(key) or {}
                if (cur.get("instance") or "legacy") == inst:
                    running_sessions.pop(key, None)
                continue
            item = dict(s)
            item["user_id"] = user_id
            item["instance"] = inst
            item["last_beat"] = now
            running_sessions[key] = item
            new_keys.add(key)
        # 本实例拥有的旧条目中，未被本次全量上报覆盖的（已结束）清除
        for k in owned - new_keys:
            running_sessions.pop(k, None)


def set_session_status(user_id: str, session_id: str, status: str,
                       info: Optional[dict] = None,
                       instance: str = "") -> None:
    """单会话状态更新（report_status running/idle）。"""
    if not user_id or not session_id:
        return
    key = (user_id, session_id)
    with _lock:
        if (status or "").lower() == "running":
            # v5.4.0+：合并语义——保留既有字段（避免信息更少的后续更新
            # 覆盖掉 title/workspace 等），空值字段不传入时自然保留
            item = dict(running_sessions.get(key) or {})
            item.update(info or {})
            item.update({"user_id": user_id, "session_id": session_id,
                         "status": "running",
                         "instance": instance or item.get("instance")
                         or "legacy",
                         "last_beat": time.time()})
            running_sessions[key] = item
        else:
            running_sessions.pop(key, None)


def drop_session(user_id: str, session_id: str) -> None:
    with _lock:
        running_sessions.pop((user_id, session_id), None)


def _cleanup() -> None:
    """清理超 TTL 条目。"""
    now = time.time()
    with _lock:
        for uid in [u for u, v in online_users.items()
                    if now - float(v.get("last_seen", 0)) > ONLINE_TTL * 4]:
            online_users.pop(uid, None)
        for k in [k for k, v in running_sessions.items()
                  if now - float(v.get("last_beat", 0)) > RUNNING_TTL * 4]:
            running_sessions.pop(k, None)


def live_snapshot() -> dict:
    """聚合输出（管理页面 /live 轮询源）。"""
    _cleanup()
    now = time.time()
    with _lock:
        users = []
        for uid, v in online_users.items():
            last_seen = float(v.get("last_seen", 0))
            client = v.get("client") or {}
            users.append({
                "user_id": uid,
                "last_seen": last_seen,
                "age": max(0.0, now - last_seen),
                "online": (now - last_seen) <= ONLINE_TTL,
                "last_seen_iso": _iso(last_seen),
                "client": client,
                "permissions": v.get("permissions") or {},
                "surfaces": v.get("surfaces") or [],
            })
        running = []
        for (uid, sid), v in running_sessions.items():
            last_beat = float(v.get("last_beat", 0))
            running.append({
                "user_id": uid,
                "session_id": sid,
                "agent": v.get("agent", ""),
                "title": v.get("title", ""),
                "workspace": v.get("workspace", ""),
                "surface": v.get("surface", ""),
                "instance": v.get("instance", "legacy"),
                "last_beat": last_beat,
                "age": max(0.0, now - last_beat),
                "running": (now - last_beat) <= RUNNING_TTL,
                "started_at": v.get("started_at", 0),
            })
    users.sort(key=lambda x: x.get("age", 0))
    running.sort(key=lambda x: x.get("last_beat", 0), reverse=True)
    return {
        "online_users": [u for u in users if u["online"]],
        "online_count": len([u for u in users if u["online"]]),
        "running_sessions": [r for r in running if r["running"]],
        "running_count": len([r for r in running if r["running"]]),
        "server_time": now,
    }


def is_online(user_id: str) -> bool:
    with _lock:
        v = online_users.get(user_id)
    if not v:
        return False
    return (time.time() - float(v.get("last_seen", 0))) <= ONLINE_TTL


def user_permissions(user_id: str) -> dict:
    with _lock:
        v = online_users.get(user_id) or {}
    return v.get("permissions") or {}


def _iso(ts: float) -> str:
    try:
        from datetime import datetime
        return datetime.fromtimestamp(ts).astimezone().isoformat(timespec="seconds")
    except Exception:
        return ""
