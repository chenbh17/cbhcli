"""管理页面实时事件推送（WebSocket /admin/ws）

- 客户端（cbhcli 各端）上报的事件经 /api/report/batch 到达后，实时
  广播给所有已认证的管理页面连接（会话实时画面 / 监控看板即时刷新）。
- 认证：连接 URL query `?token=<admin_token>`（复用 admin_api.check_admin_token）
- 协议（JSON 文本帧）：
    服务端 -> 客户端：
      {"type": "hello", "server_time": ts}
      {"type": "event", "user_id", "session_id", "event_type",
       "ts", "eid", "payload": {...}}
      {"type": "pong"}
    客户端 -> 服务端：
      {"type": "ping"}   （保活；服务端回 pong）
- 每个连接独立出站队列 + 单 sender 任务串行发送；队列积压时丢弃最旧
  事件（管理页面是观察者，不允许拖垮服务端）。
- 广播调用方：report_api.report_batch（同一事件循环内）。
"""
from __future__ import annotations

import asyncio
import json
import time
from typing import Optional

from fastapi import WebSocket, WebSocketDisconnect

_QUEUE_MAX = 2000          # 单连接出站队列上限（积压则丢最旧）


class _Conn:
    """单条管理页面连接"""

    def __init__(self, ws: WebSocket):
        self.ws = ws
        self.queue: "asyncio.Queue" = asyncio.Queue(maxsize=_QUEUE_MAX)
        self.closed = False


class AdminWSHub:
    """WebSocket 连接注册表 + 广播"""

    def __init__(self):
        self._conns: set = set()

    # ---------------------------------------------------------------
    #  连接生命周期
    # ---------------------------------------------------------------

    async def serve(self, ws: WebSocket) -> None:
        """接管一条已通过认证的 WebSocket 连接（含 sender / 接收循环）。"""
        await ws.accept()
        conn = _Conn(ws)
        self._conns.add(conn)
        sender = asyncio.create_task(self._sender_loop(conn))
        try:
            self._push(conn, {"type": "hello", "server_time": time.time()})
            while True:
                try:
                    raw = await ws.receive_text()
                except WebSocketDisconnect:
                    break
                except Exception:
                    break
                try:
                    msg = json.loads(raw)
                except Exception:
                    continue
                if isinstance(msg, dict) and msg.get("type") == "ping":
                    self._push(conn, {"type": "pong"})
        finally:
            conn.closed = True
            sender.cancel()
            self._conns.discard(conn)
            try:
                await ws.close()
            except Exception:
                pass

    async def _sender_loop(self, conn: _Conn) -> None:
        """单 sender 串行发送（避免并发 send 交错）。"""
        try:
            while not conn.closed:
                msg = await conn.queue.get()
                await conn.ws.send_json(msg)
        except asyncio.CancelledError:
            raise
        except Exception:
            conn.closed = True
            self._conns.discard(conn)

    # ---------------------------------------------------------------
    #  广播
    # ---------------------------------------------------------------

    def broadcast(self, msg: dict) -> None:
        """向所有连接推一条消息（非阻塞；不可用/积压时静默丢弃）。"""
        for conn in list(self._conns):
            self._push(conn, msg)

    @staticmethod
    def _push(conn: _Conn, msg: dict) -> None:
        if conn.closed:
            return
        try:
            conn.queue.put_nowait(msg)
        except asyncio.QueueFull:
            # 积压：丢弃最旧一条再放（观察者允许丢帧，不得阻塞上报链路）
            try:
                conn.queue.get_nowait()
            except Exception:
                pass
            try:
                conn.queue.put_nowait(msg)
            except Exception:
                pass
        except Exception:
            pass

    @property
    def conn_count(self) -> int:
        return len(self._conns)


# ===================================================================
#  单例 & 便捷函数
# ===================================================================

_hub: Optional[AdminWSHub] = None


def get_hub() -> AdminWSHub:
    global _hub
    if _hub is None:
        _hub = AdminWSHub()
    return _hub


async def broadcast_events(user_id: str, events: list) -> None:
    """把一批上报事件广播给管理页面（report_api 收到上报后调用）。

    消息体保持与上报一致的关键字段；payload 透传（≤32KB 已在入口限制）。
    """
    try:
        hub = get_hub()
        if hub.conn_count == 0:
            return
        for ev in events or []:
            if not isinstance(ev, dict):
                continue
            hub.broadcast({
                "type": "event",
                "user_id": user_id or "",
                "session_id": ev.get("session_id", "") or "",
                "event_type": str(ev.get("type", "")),
                "ts": ev.get("ts", 0),
                "eid": ev.get("eid", "") or "",
                "payload": ev.get("payload") or {},
            })
    except Exception:
        pass
