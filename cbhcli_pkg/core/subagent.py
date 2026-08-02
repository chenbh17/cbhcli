"""子Agent机制 - 临时子Agent创建、管理和执行（支持并行）"""
import io
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from cbhcli_pkg.core.session import Session, Message
from cbhcli_pkg.core.agent import AgentConfig
from cbhcli_pkg.core.constants import (
    MAX_TOOL_ROUNDS, API_TEMPERATURE, C_AI_HINT, C_AI_TEXT,
    C_DIM, C_ERROR, C_RESET,
    C_SUBAGENT_HINT, C_SUBAGENT_TEXT, C_SUBAGENT_DIM
)
from datetime import datetime
from enum import Enum
from typing import Optional, TYPE_CHECKING
import uuid

if TYPE_CHECKING:
    from cbhcli_pkg.core.model import LLMClient
    from cbhcli_pkg.core.tool_executor import ToolExecutor
    from cbhcli_pkg.context.token_counter import TokenCounter

# 并行子Agent最大并发数（超过时按波次执行）
MAX_PARALLEL_SUBAGENTS = 100


class SubAgentStatus(Enum):
    """子Agent状态"""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class SubAgent:
    """临时子Agent"""
    
    def __init__(self, name: str, parent_name: str, task: str, model_config: dict):
        """
        初始化子Agent
        
        Args:
            name: 子Agent名称
            parent_name: 父Agent名称
            task: 任务描述
            model_config: 模型配置
        """
        self.id = str(uuid.uuid4())
        self.name = name
        self.parent_name = parent_name
        self.task = task
        self.model_config = model_config
        self.session = Session(agent_name=name)
        self.status = SubAgentStatus.PENDING
        self.created_at = datetime.now()
        self.result: Optional[str] = None
    
    def start(self):
        """启动子Agent"""
        self.status = SubAgentStatus.RUNNING
    
    def complete(self, result: str):
        """完成任务"""
        self.status = SubAgentStatus.COMPLETED
        self.result = result
    
    def fail(self, error: str):
        """任务失败"""
        self.status = SubAgentStatus.FAILED
        self.result = f"错误: {error}"


class _ThreadStdoutProxy:
    """按线程分发的 stdout 代理

    工作线程的 print 输出进入各自的 StringIO 缓冲区（互不混杂），
    主线程输出直通真实 stdout（用于打印并行进度）。
    rich.Console 的 file/isatty/encoding 等属性检查透传到真实 stdout。
    """

    def __init__(self, real_stdout):
        self._real = real_stdout
        self._buffers: dict[int, io.StringIO] = {}
        self._lock = threading.Lock()

    def attach(self, ident: int, buf: io.StringIO):
        with self._lock:
            self._buffers[ident] = buf

    def detach(self, ident: int):
        with self._lock:
            self._buffers.pop(ident, None)

    def write(self, s):
        with self._lock:
            buf = self._buffers.get(threading.get_ident())
        if buf is not None:
            return buf.write(s)
        return self._real.write(s)

    def flush(self):
        with self._lock:
            buf = self._buffers.get(threading.get_ident())
        try:
            if buf is not None:
                buf.flush()
            else:
                self._real.flush()
        except Exception:
            pass

    def __getattr__(self, name):
        # isatty / encoding / fileno 等属性透传真实 stdout
        return getattr(self._real, name)


class SubAgentScheduler:
    """子Agent调度器 - 支持任务分发、独立执行和并行执行"""

    def __init__(self):
        self._active_subagents: dict[str, SubAgent] = {}
        self._spawn_lock = threading.Lock()
        self._spawn_counter = 0

    def spawn(self, parent_name: str, task: str, model_config: dict) -> SubAgent:
        """
        创建子Agent（线程安全）

        Args:
            parent_name: 父Agent名称
            task: 任务描述
            model_config: 模型配置

        Returns:
            SubAgent实例
        """
        with self._spawn_lock:
            self._spawn_counter += 1
            name = f"subagent_{self._spawn_counter}"

        sub_agent = SubAgent(name, parent_name, task, model_config)
        self._active_subagents[sub_agent.id] = sub_agent

        print(f"\n{C_SUBAGENT_HINT}[SubAgent] 创建子Agent: {name}{C_RESET}")
        print(f"{C_SUBAGENT_DIM}[SubAgent] 任务: {task}{C_RESET}")

        return sub_agent
    
    def run(
        self,
        sub_agent: SubAgent,
        llm_client: 'LLMClient',
        tool_executor: 'ToolExecutor',
        token_counter: 'TokenCounter',
        system_prompt: str = "",
        context_compressor=None,
        context_window=None,
        auto_compress: bool = True,
        parallel: bool = False
    ) -> str:
        """
        运行子Agent，执行其分配的任务

        子Agent 拥有独立的 Session，共享父 Agent 的 LLMClient 和 ToolExecutor。
        它会进入自己的 ReAct 循环，直到任务完成或达到最大轮数。

        Args:
            sub_agent: 子Agent实例
            llm_client: LLM客户端（共享父Agent的）
            tool_executor: 工具执行器（共享父Agent的）
            token_counter: Token计数器
            system_prompt: 系统提示（可选，不提供则使用默认）
            context_compressor: 上下文压缩器（可选，用于ReAct循环内自动压缩）
            context_window: 上下文窗口管理器（可选）
            auto_compress: 是否启用自动压缩
            parallel: 是否处于并行模式（禁用Live思考显示 + 禁止向用户提问）

        Returns:
            子Agent执行结果
        """
        sub_agent.start()

        # 构建子Agent的系统提示
        if not system_prompt:
            system_prompt = (
                f"你是子Agent [{sub_agent.name}]，由父Agent [{sub_agent.parent_name}] 分派任务。\n"
                f"你的任务是：{sub_agent.task}\n\n"
                "请专注完成这个子任务，完成后给出简洁的结果总结。\n"
                "你可以使用所有可用的工具来完成任务。"
            )
            if parallel:
                system_prompt += (
                    "\n注意：你正与其他子Agent并行工作，禁止向用户提问"
                    "（不要使用 ask_user 工具），请根据任务描述自主做出合理判断并完成任务。"
                )

        # 初始化子Agent的会话
        sub_agent.session.add_message(
            "system", system_prompt,
            token_count=token_counter.count_tokens(system_prompt)
        )

        print(f"\n{C_SUBAGENT_HINT}[SubAgent:{sub_agent.name}] 开始执行任务...{C_RESET}")

        try:
            # 延迟导入避免循环引用
            from cbhcli_pkg.core.ai_handler import AIHandler

            handler = AIHandler(
                llm_client=llm_client,
                session=sub_agent.session,
                tool_executor=tool_executor,
                token_counter=token_counter,
                is_subagent=True
            )

            # 注入上下文压缩组件（用于 ReAct 循环内自动压缩）
            handler.context_compressor = context_compressor
            handler.context_window = context_window
            handler.auto_compress = auto_compress

            # 并行模式：禁用 rich.Live 思考显示（其光标控制序列
            # 在线程输出捕获回放时会产生乱码）
            if parallel:
                handler.thinking_display.enabled = False

            result = handler.process_request(sub_agent.task)
            sub_agent.complete(result)

            print(f"\n{C_SUBAGENT_HINT}[SubAgent:{sub_agent.name}] 任务完成{C_RESET}")

        except Exception as e:
            error_msg = str(e)
            sub_agent.fail(error_msg)
            result = f"子Agent执行失败: {error_msg}"
            print(f"\n{C_ERROR}[SubAgent:{sub_agent.name}] 执行失败: {error_msg}{C_RESET}")

        # SubagentStop 钩子（v4.9.9：子Agent结束时触发，stdout 打印给用户）
        try:
            hook_manager = getattr(tool_executor, 'hook_manager', None)
            if hook_manager and hook_manager.has_hooks("SubagentStop"):
                decision = hook_manager.run_simple(
                    "SubagentStop",
                    extra_args={
                        "subagent_name": sub_agent.name,
                        "task": sub_agent.task[:500],
                        "success": sub_agent.status.value == "completed",
                        "result": (result or "")[:1000],
                    },
                    session_id=getattr(tool_executor, 'session_id', ""))
                from cbhcli_pkg.core.constants import C_DIM as _C_DIM
                for line in decision.outputs:
                    print(f"{_C_DIM}[hook:SubagentStop] {line}{C_RESET}")
        except Exception:
            pass  # 钩子异常绝不影响子Agent结果

        return result

    # ==================================================================
    #  并行执行
    # ==================================================================

    def run_parallel(
        self,
        parent_name: str,
        tasks: list[str],
        llm_client: 'LLMClient',
        tool_executor: 'ToolExecutor',
        token_counter: 'TokenCounter',
        context_compressor=None,
        context_window=None,
        auto_compress: bool = True,
        live_status: bool = True,
    ) -> list[dict]:
        """并行执行多个子任务，全部完成后返回结果列表

        工作流程：
        1. 安装线程分发 stdout 代理（每个工作线程的输出进入独立缓冲区）
        2. ThreadPoolExecutor 并行执行（最多 MAX_PARALLEL_SUBAGENTS 个并发）
        3. 主线程通过 rich.Live 实时状态板展示每个子Agent的当前步骤
           （不回显完整执行细则，避免多子Agent输出互相混杂）
        4. 全部完成后返回结果列表（顺序与 tasks 一致）

        Args:
            parent_name: 父Agent名称
            tasks: 子任务描述列表
            llm_client: LLM客户端
            tool_executor: 工具执行器
            token_counter: Token计数器
            context_compressor/context_window/auto_compress: 上下文压缩组件
            live_status: 是否显示实时状态板（CLI=True, Web=False）

        Returns:
            [{"name": 子Agent名, "task": 任务, "result": 结果,
              "success": 是否成功, "transcript": 执行过程文本}, ...]
        """
        import time as _time

        n = len(tasks)
        workers = min(MAX_PARALLEL_SUBAGENTS, n)

        print(f"\n{C_SUBAGENT_HINT}🚀 并行启动 {n} 个子Agent (最大并发 {workers})...{C_RESET}")

        # 每个子Agent的实时状态（工作线程更新，主线程渲染）
        statuses: list[dict] = [
            {"name": f"task{i + 1}", "task": t.split("\n")[0],
             "step": "排队中", "state": "pending", "elapsed": 0.0}
            for i, t in enumerate(tasks)
        ]
        state_lock = threading.Lock()
        # 工作线程ID → 状态索引（用于工具执行回调定位子Agent）
        ident_to_idx: dict[int, int] = {}

        def _set_status(idx: int, **kw):
            with state_lock:
                statuses[idx].update(kw)

        proxy = _ThreadStdoutProxy(sys.stdout)
        old_stdout = sys.stdout
        sys.stdout = proxy

        # 包装工具执行回调：更新对应子Agent的"当前步骤"
        old_callback = getattr(tool_executor, '_on_tool_execute', None) \
            if tool_executor else None

        def _status_hook(tool_name, arguments, result, tool_call_id):
            idx = ident_to_idx.get(threading.get_ident())
            if idx is not None:
                _set_status(idx, step=f"🔧 {tool_name}")
            if old_callback:
                old_callback(tool_name, arguments, result, tool_call_id)

        if tool_executor:
            tool_executor._on_tool_execute = _status_hook
            # 并行模式禁用 spinner 动画（stdout 已被 _ThreadStdoutProxy 按行
            # 捕获回放，spinner 的 \r/ANSI 序列会被打散成乱码）
            old_animations = getattr(tool_executor, 'animations_enabled', True)
            tool_executor.animations_enabled = False

        # rich.Live 实时状态板（仅CLI）
        live = None
        if live_status:
            live = _SubAgentLiveBoard(statuses, state_lock)
            live.start()

        results: list[Optional[dict]] = [None] * n
        start_all = _time.time()

        def _worker(idx: int, task: str) -> dict:
            """工作线程：挂载本线程输出缓冲区，执行子Agent"""
            buf = io.StringIO()
            ident = threading.get_ident()
            proxy.attach(ident, buf)
            ident_to_idx[ident] = idx
            t0 = _time.time()
            try:
                _set_status(idx, step="🚀 启动", state="running")
                sub_agent = self.spawn(parent_name, task, {})
                _set_status(idx, name=sub_agent.name, step="💭 AI思考中")
                result = self.run(
                    sub_agent, llm_client, tool_executor, token_counter,
                    context_compressor=context_compressor,
                    context_window=context_window,
                    auto_compress=auto_compress,
                    parallel=True,
                )
                ok = sub_agent.status == SubAgentStatus.COMPLETED
                _set_status(idx, state="done" if ok else "failed",
                            step="完成" if ok else "失败",
                            elapsed=_time.time() - t0)
                return {
                    "name": sub_agent.name,
                    "task": task,
                    "result": result,
                    "success": ok,
                    "transcript": buf.getvalue(),
                }
            except Exception as e:
                _set_status(idx, state="failed", step=f"异常: {str(e)[:30]}",
                            elapsed=_time.time() - t0)
                return {
                    "name": f"subagent_task{idx + 1}",
                    "task": task,
                    "result": f"子Agent执行失败: {e}",
                    "success": False,
                    "transcript": buf.getvalue(),
                }
            finally:
                proxy.detach(ident)
                ident_to_idx.pop(ident, None)

        try:
            with ThreadPoolExecutor(max_workers=workers,
                                    thread_name_prefix="cbh_subagent") as pool:
                future_map = {
                    pool.submit(_worker, i, t): i
                    for i, t in enumerate(tasks)
                }
                for future in as_completed(future_map):
                    idx = future_map[future]
                    results[idx] = future.result()
        finally:
            if live:
                live.stop()
            sys.stdout = old_stdout
            if tool_executor:
                tool_executor._on_tool_execute = old_callback
                tool_executor.animations_enabled = old_animations

        # 静态总结（状态板为 transient 模式，停止后自动清除）
        ok_count = sum(1 for r in results if r and r["success"])
        total_time = _time.time() - start_all
        print(f"\n{C_SUBAGENT_HINT}🏁 全部完成: {ok_count}/{n} 成功，"
              f"总耗时 {total_time:.1f}s{C_RESET}")
        for r in results:
            if not r:
                continue
            icon = "✅" if r["success"] else "❌"
            task_preview = r["task"].split("\n")[0][:50]
            print(f"{C_SUBAGENT_DIM}   {icon} [{r['name']}] {task_preview}{C_RESET}")

        return results

    def delegate_and_run(
        self,
        parent_name: str,
        task: str,
        model_config: dict,
        llm_client: 'LLMClient',
        tool_executor: 'ToolExecutor',
        token_counter: 'TokenCounter',
        system_prompt: str = "",
        context_compressor=None,
        context_window=None,
        auto_compress: bool = True
    ) -> str:
        """
        一步完成创建 + 执行子Agent

        Args:
            parent_name: 父Agent名称
            task: 任务描述
            model_config: 模型配置
            llm_client: LLM客户端
            tool_executor: 工具执行器
            token_counter: Token计数器
            system_prompt: 系统提示（可选）
            context_compressor: 上下文压缩器（可选）
            context_window: 上下文窗口管理器（可选）
            auto_compress: 是否启用自动压缩

        Returns:
            子Agent执行结果
        """
        sub_agent = self.spawn(parent_name, task, model_config)
        result = self.run(sub_agent, llm_client, tool_executor, token_counter,
                          system_prompt, context_compressor, context_window,
                          auto_compress)
        return result

    def get_result(self, sub_agent_id: str) -> str:
        """
        获取子Agent结果

        Args:
            sub_agent_id: 子Agent ID

        Returns:
            执行结果
        """
        sub_agent = self._active_subagents.get(sub_agent_id)

        if not sub_agent:
            return "错误: 子Agent不存在"

        if sub_agent.status == SubAgentStatus.COMPLETED:
            return f"子Agent [{sub_agent.name}] 结果:\n{sub_agent.result}"
        elif sub_agent.status == SubAgentStatus.FAILED:
            return f"子Agent [{sub_agent.name}] 失败:\n{sub_agent.result}"
        else:
            return "子Agent仍在运行中"

    def cleanup(self, sub_agent_id: str) -> None:
        """
        清理子Agent

        Args:
            sub_agent_id: 子Agent ID
        """
        if sub_agent_id in self._active_subagents:
            del self._active_subagents[sub_agent_id]

    def cleanup_all(self) -> int:
        """清理所有已完成/失败的子Agent，返回清理数量"""
        to_remove = [
            sid for sid, sa in self._active_subagents.items()
            if sa.status in (SubAgentStatus.COMPLETED, SubAgentStatus.FAILED)
        ]
        for sid in to_remove:
            del self._active_subagents[sid]
        return len(to_remove)

    def get_active_count(self) -> int:
        """获取活跃子Agent数量"""
        return len(self._active_subagents)

    def get_status_summary(self) -> str:
        """获取所有子Agent的状态摘要"""
        if not self._active_subagents:
            return "没有活跃的子Agent"

        lines = []
        for sa in self._active_subagents.values():
            status_icon = {
                SubAgentStatus.PENDING: "⏳",
                SubAgentStatus.RUNNING: "🔄",
                SubAgentStatus.COMPLETED: "✅",
                SubAgentStatus.FAILED: "❌"
            }.get(sa.status, "?")
            lines.append(f"  {status_icon} [{sa.name}] {sa.task[:50]} - {sa.status.value}")

        return "\n".join(lines)


class _SubAgentLiveBoard:
    """并行子Agent实时状态板（rich.Live 动态渲染）

    显示每个子Agent的：名称、任务、当前步骤、状态。
    工作线程更新共享状态字典，Live 自动刷新渲染。
    transient 模式：停止后状态板自动从终端清除，不占用滚动历史。
    """

    def __init__(self, statuses: list[dict], lock: threading.Lock):
        from rich.console import Console
        from rich.live import Live
        self._statuses = statuses
        self._lock = lock
        self._console = Console()
        self._live = Live(
            self, console=self._console,
            refresh_per_second=4, transient=True,
        )

    def start(self):
        try:
            self._live.start()
        except Exception:
            pass

    def stop(self):
        try:
            self._live.stop()
        except Exception:
            pass

    def __rich_console__(self, console, options):
        from rich.table import Table
        from rich.text import Text

        with self._lock:
            snapshot = [dict(s) for s in self._statuses]

        total = len(snapshot)
        done = sum(1 for s in snapshot if s["state"] in ("done", "failed"))
        running = sum(1 for s in snapshot if s["state"] == "running")

        yield Text(f"🔄 并行子Agent执行中  完成 {done}/{total}  "
                   f"运行 {running}  排队 {total - done - running}",
                   style="bold cyan")

        table = Table(box=None, padding=(0, 2), expand=False, highlight=False)
        table.add_column("子Agent", style="bold magenta", no_wrap=True)
        table.add_column("任务", overflow="fold", ratio=3)
        table.add_column("当前步骤", overflow="fold", ratio=2)
        table.add_column("状态", no_wrap=True)

        state_map = {
            "pending": ("⏳ 排队", "dim"),
            "running": ("🔄 运行", "cyan"),
            "done": ("✅ 完成", "green"),
            "failed": ("❌ 失败", "red"),
        }
        for s in snapshot:
            icon, style = state_map.get(s["state"], (s["state"], ""))
            task = s["task"][:40]
            step = s["step"][:30]
            if s["state"] in ("done", "failed") and s.get("elapsed"):
                step = f"{step} ({s['elapsed']:.1f}s)"
            table.add_row(s["name"], task, step, Text(icon, style=style))
        yield table
