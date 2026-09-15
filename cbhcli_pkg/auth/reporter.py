"""AuditReporter：审计上报（事件队列 / 心跳 / 落盘重试 / 快照）

设计原则（继承 core/web_notify.py 的全部教训）：
1. 绝不阻塞、绝不抛异常、绝不静默丢进程退出前的最后一批（stop 同步 flush）
2. 单例：get_reporter() 懒初始化；四端（含 jupyter 进程）共用
3. 网络层 urllib，超时 3s；失败静默 + 指数退避 + 落盘重试

线程模型：
- 1 个上报线程：queue.get(timeout=2) 聚批（≤100 条/批）发送；
  失败 -> 指数退避（1/2/4...最大 60s）+ 连续失败 ≥5 次落盘
- 1 个心跳线程：每 policy.heartbeat_interval（默认 60s）调 /api/auth/heartbeat，
  携带本进程 running 会话集合 + 权限快照

落盘重试队列：~/.cbhcli/report_queue/<timestamp>_<pid>.jsonl
- 下次 start() 时按文件名顺序重放（发送成功后删除文件）
- 总容量上限 20MB，超限丢最旧文件并记一条 report_dropped 事件

多进程说明：CLI/Web/Jupyter/exec 是不同进程，各自实例化（不共享内存），
都基于同一 auth.json token 上报；服务端按 (user_id, session_id) 聚合。
"""
from __future__ import annotations

import json
import os
import queue
import threading
import time
import uuid
from pathlib import Path
from typing import Optional

from cbhcli_pkg.auth import events as _events
from cbhcli_pkg.auth.client import AuthClient, AuthError
from cbhcli_pkg.auth.store import AuthStore

try:
    from cbhcli_pkg.config.global_config import CBHCLI_DIR
except Exception:  # pragma: no cover
    CBHCLI_DIR = Path(os.path.expanduser("~/.cbhcli"))

_QUEUE_DIR = Path(str(CBHCLI_DIR)) / "report_queue"
_QUEUE_DIR_MAX_BYTES = 20 * 1024 * 1024     # 落盘队列总容量上限
_BATCH_MAX = 100                            # 单批事件数上限
_BATCH_WAIT = 2.0                           # 聚批等待（秒）
_FAILS_TO_SPILL = 5                         # 连续失败多少次后开始落盘
_FLUSH_TIMEOUT = 3.0                        # 退出时同步 flush 超时
_HEARTBEAT_DEFAULT = 60                     # 默认心跳间隔（秒）
_BACKOFF_MAX = 60.0

_reporter_singleton: Optional["AuditReporter"] = None
_singleton_lock = threading.Lock()


class AuditReporter:
    """审计上报器（线程安全；未登录/未 start 时所有操作静默忽略）"""

    def __init__(self, surface: str = "cli"):
        self.surface = surface
        self._lock = threading.Lock()
        self._started = False
        self._stopped = False
        self._queue: "queue.Queue[dict]" = queue.Queue()
        self._running: dict = {}            # session_id -> status dict
        self._snap_dedup: dict = {}         # session_id -> (updated_at, message_count)
        self._surfaces: set = set()
        self._report_thread: Optional[threading.Thread] = None
        self._hb_thread: Optional[threading.Thread] = None
        self._hb_stop = threading.Event()
        self._fail_count = 0
        self._queue_file: Optional[Path] = None
        self._queue_write_fails = 0
        self._spilling = False
        # 进程实例标识（v5.4.0：服务端据此隔离多进程的运行中会话集合，
        # 修复"Web/CLI/Jupyter 心跳互相覆盖导致看板会话闪烁/消失"）
        self._instance_id = "i_" + uuid.uuid4().hex[:12]
        # 流式内容聚合缓冲：(session_id, kind) -> {"text", "last"}
        self._stream_buf: dict = {}

    # ===============================================================
    #  生命周期
    # ===============================================================

    def start(self, surface: str = "cli") -> None:
        """进程启动时调用：启动上报/心跳线程 + 重放落盘队列（幂等）。"""
        with self._lock:
            self._surfaces.add(surface or "cli")
            self.surface = surface or self.surface
            if self._started or self._stopped:
                return
            self._started = True
        # 未登录：线程不启动（登录成功后 login 流程会再次调用 start）
        if not AuthStore().is_logged_in():
            return
        # 进程退出兜底：atexit 同步 flush 最后一批事件（8.4）
        try:
            import atexit
            atexit.register(self.stop)
        except Exception:
            pass
        self._replay_spilled_async()
        self._report_thread = threading.Thread(
            target=self._report_loop, name="cbhcli-audit-report", daemon=True)
        self._report_thread.start()
        self._hb_thread = threading.Thread(
            target=self._heartbeat_loop, name="cbhcli-audit-heartbeat", daemon=True)
        self._hb_thread.start()

    def stop(self) -> None:
        """进程退出时调用（atexit 兜底）：同步 flush 最后一批事件。"""
        with self._lock:
            if self._stopped:
                return
            started = self._started
        self._hb_stop.set()
        if not started:
            with self._lock:
                self._stopped = True
            return
        # 先冲刷流式聚合缓冲（此时 report_event 仍接受入队）
        try:
            self.flush_stream()
        except Exception:
            pass
        with self._lock:
            self._stopped = True
        try:
            self._flush_sync()
        except Exception:
            pass

    @property
    def started(self) -> bool:
        return self._started

    # ===============================================================
    #  入队 API（全部 O(1) 线程安全，绝不阻塞）
    # ===============================================================

    def report_event(self, type_: str, session_id: str = "", **payload) -> None:
        """事件入队（未启动/未登录时忽略）。"""
        if not self._started or self._stopped:
            return
        try:
            ev = _events.make_event(type_, session_id=session_id, payload=payload)
            self._queue.put(ev)
        except Exception:
            pass

    def report_status(self, session_id: str, agent: str = "", title: str = "",
                      workspace: str = "", status: str = "running",
                      surface: str = "") -> None:
        """会话状态入队 + 更新本进程 running 集合（心跳兜底收敛）。

        另外立即上报一条轻量 session_status 事件，让管理页面"运行中"
        秒级上屏（心跳 60s 周期兜底收敛）。
        """
        if not session_id:
            return
        # 轮开始/结束边界：先冲刷流式聚合缓冲，避免跨轮串文本
        try:
            self.flush_stream(session_id)
        except Exception:
            pass
        try:
            with self._lock:
                if status == "running":
                    self._running[session_id] = {
                        "session_id": session_id,
                        "agent": agent or "main",
                        "title": (title or "")[:200],
                        "workspace": workspace or "",
                        "status": "running",
                        "surface": surface or self.surface,
                        "updated": time.time(),
                    }
                else:
                    # v5.4.0+：轮结束（idle）时从运行条目兜底 title/workspace/
                    # agent——CLI 的 idle 调用不带这些字段，此前会让管理端
                    # 用空值覆盖已有会话标题（列表标题变空）
                    prev = self._running.pop(session_id, None) or {}
                    if not title:
                        title = prev.get("title", "")
                    if not workspace:
                        workspace = prev.get("workspace", "")
                    if not agent:
                        agent = prev.get("agent", "")
                    if not surface:
                        surface = prev.get("surface", "")
        except Exception:
            pass
        # 即时状态事件（轻量；report_status 在对话开始/结束各调一次）
        try:
            self.report_event("session_status", session_id=session_id,
                              status=status, agent=agent or "main",
                              title=(title or "")[:200],
                              workspace=workspace or "",
                              surface=surface or self.surface,
                              instance_id=self._instance_id)
        except Exception:
            pass

    def note_session_end(self, session_id: str) -> None:
        """会话结束：发 idle + 从运行集合移除。"""
        if not session_id:
            return
        self.report_status(session_id, status="idle")
        self.report_event("session_end", session_id=session_id, reason="quit")

    # ===============================================================
    #  流式内容聚合（v5.4.0：管理页面"实时交互画面"数据源）
    # ===============================================================

    # 聚合阈值：块大小 / 静置时间（逐 token 增量聚合后再上报，
    # 避免打爆上报链路；轮开始/结束与流结束时冲刷确保内容及时上屏）
    # v5.4.0 迭代：0.8s/400 字符 → 0.18s/120 字符（管理页面实时画面
    # 接近流式体验；单轮长回复约每 0.18s 一条事件，量级仍很小）
    _STREAM_FLUSH_CHARS = 120
    _STREAM_FLUSH_SECS = 0.18
    _STREAM_IDLE_SECS = 0.8

    def report_stream_text(self, session_id: str, kind: str, text: str) -> None:
        """流式内容聚合上报（kind: content / reasoning）。

        逐 token 的增量先入缓冲：≥400 字符 或 距上次冲刷 ≥0.8s 时
        上报一条 stream_chunk 事件（管理页面实时渲染 AI 思考/正文）。
        """
        if not self._started or self._stopped or not session_id or not text:
            return
        key = (session_id, kind)
        now = time.time()
        flush_text = ""
        try:
            with self._lock:
                item = self._stream_buf.get(key)
                if item is None:
                    item = {"text": "", "last": now}
                    self._stream_buf[key] = item
                item["text"] += text
                if (len(item["text"]) >= self._STREAM_FLUSH_CHARS
                        or now - float(item["last"]) >= self._STREAM_FLUSH_SECS):
                    flush_text = item["text"]
                    item["text"] = ""
                    item["last"] = now
        except Exception:
            return
        if flush_text:
            self.report_event("stream_chunk", session_id=session_id,
                              kind=kind, text=flush_text[:4000])

    def flush_stream(self, session_id: str = "") -> None:
        """冲刷流式聚合缓冲（轮边界 / 流结束 / 进程退出；空参=全部）。"""
        if not self._stream_buf:
            return
        pending = []
        try:
            with self._lock:
                for key, item in list(self._stream_buf.items()):
                    if session_id and key[0] != session_id:
                        continue
                    if item.get("text"):
                        pending.append((key, item["text"]))
                        item["text"] = ""
                        item["last"] = time.time()
        except Exception:
            return
        for (sid, kind), text in pending:
            try:
                self.report_event("stream_chunk", session_id=sid,
                                  kind=kind, text=text[:4000])
            except Exception:
                pass

    def _flush_stale_streams(self) -> None:
        """兜底：把静置超过 _STREAM_IDLE_SECS 的聚合块冲刷（上报线程周期调用）。"""
        if not self._stream_buf:
            return
        now = time.time()
        pending = []
        try:
            with self._lock:
                for key, item in list(self._stream_buf.items()):
                    if item.get("text") and \
                            now - float(item.get("last", 0)) >= self._STREAM_IDLE_SECS:
                        pending.append((key, item["text"]))
                        item["text"] = ""
                        item["last"] = now
        except Exception:
            return
        for (sid, kind), text in pending:
            try:
                self.report_event("stream_chunk", session_id=sid,
                                  kind=kind, text=text[:4000])
            except Exception:
                pass

    # ===============================================================
    #  快照
    # ===============================================================

    def report_snapshot(self, session_file_or_data, agent: str = "",
                        session_id: str = "", surface: str = "") -> None:
        """快照上报（文件路径或 dict 数据；去重 + 异步上传）。

        - 去重键: session_id -> (updated_at, message_count)，未变化跳过
        - 超过 policy.snapshot_max_mb 跳过并记告警事件
        """
        if not self._started or self._stopped:
            return
        try:
            if isinstance(session_file_or_data, (str, Path)):
                p = Path(session_file_or_data)
                if not p.exists():
                    return
                data = json.loads(p.read_text(encoding="utf-8"))
            elif isinstance(session_file_or_data, dict):
                data = session_file_or_data
            else:
                return
            if not isinstance(data, dict) or "messages" not in data:
                return

            sid = session_id or str(data.get("id", "")) or ""
            if not sid:
                return
            updated_at = str(data.get("updated_at", "")) or ""
            msg_count = int(data.get("message_count", 0) or 0)

            with self._lock:
                prev = self._snap_dedup.get(sid)
            if prev == (updated_at, msg_count):
                return  # 未变化，不传

            # 大小上限（policy 下发；默认 10MB）
            max_mb = self._snapshot_max_mb()
            try:
                size = len(json.dumps(data, ensure_ascii=False).encode("utf-8"))
            except Exception:
                size = 0
            if size > max_mb * 1024 * 1024:
                self.report_event("snapshot_skipped", session_id=sid,
                                  reason="too_large", size_bytes=size,
                                  limit_mb=max_mb)
                with self._lock:
                    self._snap_dedup[sid] = (updated_at, msg_count)
                return

            with self._lock:
                self._snap_dedup[sid] = (updated_at, msg_count)
            threading.Thread(
                target=self._upload_snapshot,
                args=(sid, agent or "main", surface or self.surface,
                      updated_at, msg_count, data),
                name="cbhcli-audit-snapshot", daemon=True).start()
        except Exception:
            pass

    def _snapshot_max_mb(self) -> float:
        try:
            data = AuthStore().load() or {}
            policy = data.get("policy") or {}
            return float(policy.get("snapshot_max_mb", 10) or 10)
        except Exception:
            return 10.0

    def _upload_snapshot(self, sid: str, agent: str, surface: str,
                         updated_at: str, msg_count: int, data: dict) -> None:
        try:
            AuthClient().report_snapshot(
                session_id=sid, agent=agent, surface=surface,
                updated_at=updated_at, message_count=msg_count, data=data)
        except Exception:
            # 快照失败不重试（低频重型；下一轮变化时会再次触发）
            pass

    # ===============================================================
    #  上报线程
    # ===============================================================

    def _report_loop(self) -> None:
        backoff = 1.0
        while not self._stopped:
            self._flush_stale_streams()     # 流式内容兜底冲刷（静置超时）
            batch = self._drain_batch()
            if not batch:
                continue
            ok = self._send_batch(batch)
            if ok:
                backoff = 1.0
                self._fail_count = 0
            else:
                self._fail_count += 1
                with self._lock:
                    self._spilling = True
                self._spill(batch)      # 失败即落盘（内存优先，但失败不能丢）
                time.sleep(backoff)
                backoff = min(backoff * 2, _BACKOFF_MAX)

    def _drain_batch(self) -> list:
        """聚批取事件（首条阻塞等待 _BATCH_WAIT，其余非阻塞取到满批）。"""
        batch: list = []
        try:
            first = self._queue.get(timeout=_BATCH_WAIT)
            if isinstance(first, dict):
                batch.append(first)
        except queue.Empty:
            return batch
        while len(batch) < _BATCH_MAX:
            try:
                ev = self._queue.get_nowait()
            except queue.Empty:
                break
            if isinstance(ev, dict):
                batch.append(ev)
        return batch

    def _send_batch(self, batch: list) -> bool:
        try:
            AuthClient().report_batch(batch)
            return True
        except AuthError as e:
            if e.code in ("unreachable", "timeout", "tls_error", "http_error",
                          "server_error"):
                try:
                    from cbhcli_pkg.auth.guard import mark_offline
                    mark_offline()
                except Exception:
                    pass
            return False
        except Exception:
            return False

    # ===============================================================
    #  心跳线程
    # ===============================================================

    def _heartbeat_loop(self) -> None:
        interval = self._heartbeat_interval()
        # 启动即打一次心跳（上线通知）
        while not self._hb_stop.wait(interval):
            try:
                self._send_heartbeat()
            except Exception:
                pass

    def _heartbeat_interval(self) -> float:
        try:
            data = AuthStore().load() or {}
            policy = data.get("policy") or {}
            return max(10.0, float(policy.get("heartbeat_interval", _HEARTBEAT_DEFAULT)
                                   or _HEARTBEAT_DEFAULT))
        except Exception:
            return float(_HEARTBEAT_DEFAULT)

    def _collect_running(self) -> list:
        with self._lock:
            return [dict(v) for v in self._running.values()]

    def _send_heartbeat(self) -> None:
        if not self._started or self._stopped:
            return
        try:
            perms = _events.collect_permissions()
            perms["surfaces"] = sorted(self._surfaces)
            resp = AuthClient().heartbeat(
                running_sessions=self._collect_running(),
                permissions=perms,
                client_extra={"surface": self.surface,
                              "status": "online"},
                instance_id=self._instance_id)
            # 滚动续期（心跳也带 new_token / 最新有效期）
            try:
                from cbhcli_pkg.auth.guard import _apply_renew, mark_online
                _apply_renew(resp, AuthStore())
                mark_online()
            except Exception:
                pass
        except AuthError as e:
            try:
                from cbhcli_pkg.auth.guard import mark_offline
                mark_offline()
            except Exception:
                pass
            if e.code in ("invalid_key", "disabled", "not_logged_in"):
                # token 失效/被禁用：记事件（本地），后续 guard 会拦截
                self.report_event(_events.EV_VALIDATE_FAILED, reason=e.code)
        except Exception:
            pass

    # ===============================================================
    #  落盘重试队列
    # ===============================================================

    def _queue_file_path(self) -> Path:
        return _QUEUE_DIR / f"{int(time.time())}_{os.getpid()}.jsonl"

    def _spill(self, batch: list) -> None:
        """发送失败的事件落盘（追加写；单文件 + 总容量控制）。"""
        try:
            _QUEUE_DIR.mkdir(parents=True, exist_ok=True)
            if self._queue_file is None:
                self._queue_file = self._queue_file_path()
            with open(self._queue_file, "a", encoding="utf-8") as f:
                for ev in batch:
                    try:
                        f.write(json.dumps(ev, ensure_ascii=False) + "\n")
                    except Exception:
                        continue
            self._enforce_queue_capacity()
        except Exception:
            pass

    def _enforce_queue_capacity(self) -> None:
        """落盘队列总容量上限：超限丢最旧文件并记 report_dropped。"""
        try:
            files = sorted(_QUEUE_DIR.glob("*.jsonl"))
            total = sum(f.stat().st_size for f in files if f.exists())
            dropped = 0
            while total > _QUEUE_DIR_MAX_BYTES and files:
                oldest = files.pop(0)
                try:
                    total -= oldest.stat().st_size
                    oldest.unlink()
                    dropped += 1
                except Exception:
                    break
            if dropped:
                self.report_event(_events.EV_REPORT_DROPPED, count=dropped)
        except Exception:
            pass

    def _replay_spilled_async(self) -> None:
        threading.Thread(target=self._replay_spilled, name="cbhcli-audit-replay",
                         daemon=True).start()

    def _replay_spilled(self) -> None:
        """重放落盘队列（按文件名顺序；每个文件发送成功后删除）。"""
        try:
            if not _QUEUE_DIR.exists():
                return
            for f in sorted(_QUEUE_DIR.glob("*.jsonl")):
                try:
                    evs = []
                    for line in f.read_text(encoding="utf-8").splitlines():
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            evs.append(json.loads(line))
                        except Exception:
                            continue
                    if not evs:
                        f.unlink()
                        continue
                    sent_all = True
                    for i in range(0, len(evs), _BATCH_MAX):
                        chunk = evs[i:i + _BATCH_MAX]
                        if not self._send_batch(chunk):
                            sent_all = False
                            break
                    if sent_all:
                        f.unlink()
                    else:
                        break  # 服务仍不可达：保留剩余文件，下次再试
                except Exception:
                    continue
        except Exception:
            pass

    # ===============================================================
    #  退出同步 flush
    # ===============================================================

    def _flush_sync(self) -> None:
        """同步发送队列中剩余事件（3s 超时；进程退出路径专用）。"""
        pending: list = []
        while len(pending) < _BATCH_MAX * 10:
            try:
                ev = self._queue.get_nowait()
            except queue.Empty:
                break
            if isinstance(ev, dict):
                pending.append(ev)
        if not pending:
            return
        for i in range(0, len(pending), _BATCH_MAX):
            chunk = pending[i:i + _BATCH_MAX]
            try:
                AuthClient(timeout=_FLUSH_TIMEOUT).report_batch(chunk)
            except Exception:
                self._spill(chunk)      # 退出时也发不出去 -> 落盘给下次启动
                break


# ===================================================================
#  单例 & 便捷函数
# ===================================================================

def get_reporter() -> AuditReporter:
    global _reporter_singleton
    if _reporter_singleton is None:
        with _singleton_lock:
            if _reporter_singleton is None:
                _reporter_singleton = AuditReporter()
    return _reporter_singleton


def report_snapshot_file(filepath, agent: str = "", session_id: str = "") -> None:
    """模块级便捷函数：读会话 JSON 文件并上报快照（6.2 改点 2 用）。"""
    try:
        get_reporter().report_snapshot(filepath, agent=agent, session_id=session_id)
    except Exception:
        pass


def report_event(type_: str, session_id: str = "", **payload) -> None:
    """模块级便捷函数：事件入队。"""
    try:
        get_reporter().report_event(type_, session_id=session_id, **payload)
    except Exception:
        pass


def report_stream_text(session_id: str, kind: str, text: str) -> None:
    """模块级便捷函数：流式内容聚合上报（kind: content / reasoning）。"""
    try:
        get_reporter().report_stream_text(session_id, kind, text)
    except Exception:
        pass


def flush_stream_text(session_id: str = "") -> None:
    """模块级便捷函数：冲刷流式聚合缓冲（流结束/轮边界）。"""
    try:
        get_reporter().flush_stream(session_id)
    except Exception:
        pass
