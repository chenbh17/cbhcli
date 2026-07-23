"""调用链追踪器 — Harness 可观测性层

将会话中的关键事件以 JSONL 形式追加到 Agent 工作空间：
  ~/.cbhcli/agents/<agent>/history/traces/<session_id>.jsonl

每行一个事件：
  {"ts", "event", "session_id", ...}

事件类型：
  tool_call       工具调用（名称/参数摘要/权限判定/耗时/成功）
  tool_blocked    工具被拦截（deny 规则 / hooks / 用户取消）
  loop_detected   死循环检测（warn/block/abort）
  mode_change     权限模式切换
  compress        上下文压缩
  fallback        备用模型切换
  text_loop       文本复读截断

设计原则：只追加、不读取、永不抛异常——可观测性不能影响主流程。
"""
from __future__ import annotations

import json
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional


class Tracer:
    """轻量 JSONL 调用链追踪器（线程安全，异常静默）"""

    def __init__(self, workspace_path: Optional[Path] = None,
                 session_id: str = ""):
        self._lock = threading.Lock()
        self._file: Optional[Path] = None
        self.session_id = session_id or "unknown"
        if workspace_path:
            try:
                trace_dir = Path(workspace_path) / "history" / "traces"
                trace_dir.mkdir(parents=True, exist_ok=True)
                self._file = trace_dir / f"{self.session_id}.jsonl"
            except Exception:
                self._file = None

    @staticmethod
    def _summarize(value, limit: int = 300):
        """参数/结果摘要（截断防爆）"""
        try:
            text = json.dumps(value, ensure_ascii=False) \
                if isinstance(value, (dict, list)) else str(value)
        except Exception:
            text = repr(value)
        return text[:limit]

    def log(self, event: str, **data):
        """追加一条事件（任何异常都静默吞掉）"""
        if not self._file:
            return
        record = {
            "ts": datetime.now().isoformat(timespec="milliseconds"),
            "event": event,
            "session_id": self.session_id,
        }
        for k, v in data.items():
            record[k] = self._summarize(v) if isinstance(v, (dict, list)) else v
        try:
            with self._lock:
                with open(self._file, "a", encoding="utf-8") as f:
                    f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except Exception:
            pass

    # --------------------------------------------------------------
    # 语义化便捷方法
    # --------------------------------------------------------------

    def log_tool_call(self, tool_name: str, arguments: dict,
                      permission: str = "", duration_ms: int = 0,
                      success: bool = True, error: str = ""):
        self.log("tool_call",
                 tool=tool_name,
                 arguments=self._summarize(arguments),
                 permission=permission,
                 duration_ms=duration_ms,
                 success=success,
                 error=error[:300] if error else "")

    def log_tool_blocked(self, tool_name: str, arguments: dict,
                         reason: str, source: str):
        """source: permission | hook | user | loop"""
        self.log("tool_blocked",
                 tool=tool_name,
                 arguments=self._summarize(arguments),
                 reason=reason[:300],
                 source=source)

    def log_loop(self, verdict: str, tool_name: str = "", detail: str = ""):
        self.log("loop_detected", verdict=verdict, tool=tool_name,
                 detail=detail[:300])

    def log_mode_change(self, old_mode: str, new_mode: str):
        self.log("mode_change", old_mode=old_mode, new_mode=new_mode)

    def log_compress(self, success: bool, before_tokens: int = 0,
                     after_tokens: int = 0):
        self.log("compress", success=success,
                 before_tokens=before_tokens, after_tokens=after_tokens)

    def log_fallback(self, from_model: str, to_model: str, reason: str = ""):
        self.log("fallback", from_model=from_model, to_model=to_model,
                 reason=reason[:200])
