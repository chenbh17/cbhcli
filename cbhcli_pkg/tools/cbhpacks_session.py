"""cbhpacks 工具会话状态管理

与 python 工具共享 PythonSession 命名空间：
- cbhpacks 实例缓存存于会话命名空间的 '_cbhpacks_cache' 中（按工具名隔离）
- 每次执行的结果变量通过 _expose() 注入会话，python 工具可直接使用
- /new 或 /reset 时 remove_python_session() 销毁会话，所有缓存与变量自动释放
"""
import threading

from cbhcli_pkg.tools.python_tool import get_python_session
from cbhcli_pkg.tools.registry import BaseTool


class CbhpacksSessionTool(BaseTool):
    """cbhpacks 工具基类 - 会话级状态缓存（与 python 工具共享命名空间）"""

    def __init__(self, session_id: str = "default"):
        self._session_id = session_id
        self._tracer = None  # Harness 审计（由 tool_executor 注入，可选）
        # 修复(v5.3.1) Bug 8：findings 收集器改线程本地存储——
        # 并行子Agent(delegate_task)共享同一工具实例，实例级 buffer 会互相
        # 取错/清空 findings；threading.local 使每个工作线程独立收集
        self._tls = threading.local()

    @property
    def _findings_buffer(self) -> list:
        """本次 execute 的 findings 收集器（线程本地，按线程惰性初始化）"""
        buf = getattr(self._tls, "findings_buffer", None)
        if buf is None:
            buf = []
            self._tls.findings_buffer = buf
        return buf

    @_findings_buffer.setter
    def _findings_buffer(self, value: list):
        self._tls.findings_buffer = list(value)

    def set_session_id(self, session_id: str):
        """设置会话ID（与 PythonTool 保持一致）"""
        self._session_id = session_id

    def set_tracer(self, tracer):
        """注入 Tracer（由 tool_executor 在执行前调用，None 时审计静默跳过）"""
        self._tracer = tracer

    def _log_findings(self, findings: list):
        """把 Harness 检查发现写入审计日志（harness_check 事件，永不抛异常）

        同时累积到 _findings_buffer，由 _pop_findings() 挂到 ToolResult.harness_findings
        （供 CLI 醒目横幅 / Web 卡片徽标 / PostToolUse hooks 消费）。
        """
        if findings:
            self._findings_buffer.extend(findings)
        if not self._tracer or not findings:
            return
        try:
            for f in findings:
                self._tracer.log(
                    "harness_check",
                    level=f.level,
                    code=f.code,
                    message=f.message[:300],
                )
        except Exception:
            pass

    def _pop_findings(self):
        """取走本次 execute 累积的 findings（序列化为 dict 列表，无则返回 None）"""
        if not self._findings_buffer:
            return None
        out = [{"level": f.level, "code": f.code,
                "message": f.message[:300], "fix": getattr(f, "fix", "") or ""}
               for f in self._findings_buffer]
        self._findings_buffer = []
        return out

    @property
    def _session_globals(self) -> dict:
        """当前会话的全局命名空间"""
        return get_python_session(self._session_id)._globals

    def _get_cache(self, tool_key: str) -> dict:
        """获取本工具的会话级实例缓存（按工具名隔离，随会话销毁自动释放）"""
        cache = self._session_globals.setdefault('_cbhpacks_cache', {})
        return cache.setdefault(tool_key, {})

    def _expose(self, **variables):
        """把结果变量注入 python 会话命名空间，python 工具中可直接使用"""
        self._session_globals.update(variables)
