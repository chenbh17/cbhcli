"""任务委托工具 - 允许Agent将子任务委托给子Agent执行（支持串行/并行）"""
import io
import re
import sys
from cbhcli_pkg.tools.registry import BaseTool, ToolResult


# ANSI escape code pattern
_ANSI_RE = re.compile(r'\x1b\[[0-9;]*m')

# 并行模式单次最多允许的子任务数量
MAX_TASKS = 100


class DelegateTaskTool(BaseTool):
    """任务委托工具

    允许主Agent将子任务委托给子Agent执行：
    - 传入 task（单个任务）→ 串行执行
    - 传入 tasks（任务列表）→ 多个子Agent并行执行，全部完成后汇总返回
    子Agent拥有独立的会话上下文，共享主Agent的工具和LLM。
    """

    def __init__(self, app):
        """
        Args:
            app: CBHCLIApp 实例
        """
        self._app = app

    @property
    def name(self) -> str:
        return "delegate_task"

    @property
    def description(self) -> str:
        return (
            "将一个或多个相互独立的子任务委托给子Agent执行，子Agent拥有独立上下文。"
            "传入 task（单个任务描述）时串行执行；"
            "传入 tasks（任务列表）时多个子Agent并行执行，全部完成后一次性返回汇总结果（主Agent再继续）。"
            "请根据任务情况智能选择：只有1个子任务、或子任务间有依赖关系（一个的输出是另一个的输入）时用 task 串行；"
            "有多个互不依赖的独立子任务时用 tasks 并行（最多100个并发），可显著缩短总耗时。"
            "注意：1) 子Agent无法访问当前对话历史，每个任务描述必须完整独立、包含足够上下文；"
            "2) 并行执行期间子Agent的工具调用将自动确认（不再逐个询问）。"
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "task": {
                    "type": "string",
                    "description": "单个子任务的完整描述（串行模式），需包含足够上下文让子Agent独立完成"
                },
                "context": {
                    "type": "string",
                    "description": "可选的额外上下文信息，如文件路径、目标要求等（串行模式）"
                },
                "tasks": {
                    "type": "array",
                    "description": "子任务列表（并行模式），建议2~4个，最多100个，每个子任务必须完整独立、互不依赖",
                    "items": {
                        "type": "object",
                        "properties": {
                            "task": {
                                "type": "string",
                                "description": "子任务的完整描述"
                            },
                            "context": {
                                "type": "string",
                                "description": "可选的额外上下文信息"
                            }
                        },
                        "required": ["task"]
                    }
                }
            },
            "required": []
        }

    def execute(self, task: str = "", context: str = "",
                tasks: list = None, **kwargs) -> ToolResult:
        """执行任务委托

        Args:
            task: 单个子任务描述（串行模式）
            context: 额外上下文（串行模式）
            tasks: 子任务列表（并行模式）

        Returns:
            ToolResult: 子Agent执行结果
        """
        # 兼容：tasks 被误传为单个 dict 时自动包装为列表
        if isinstance(tasks, dict):
            tasks = [tasks]

        if tasks:
            return self._execute_parallel(tasks)
        if task:
            return self._execute_serial(task, context)
        return ToolResult(
            success=False, output="",
            error="必须提供 task（串行执行）或 tasks（并行执行）参数之一"
        )

    # ==================================================================
    #  串行模式（单个子任务）
    # ==================================================================

    def _execute_serial(self, task: str, context: str = "") -> ToolResult:
        """串行执行单个子任务（原始行为）"""
        scheduler = getattr(self._app, 'subagent_scheduler', None)
        if not scheduler:
            return ToolResult(
                success=False, output="", error="子Agent调度器未初始化")

        llm_client = getattr(self._app, 'llm_client', None)
        if not llm_client:
            return ToolResult(
                success=False, output="", error="LLM客户端未初始化")

        tool_executor = getattr(self._app, 'tool_executor', None)
        token_counter = getattr(self._app, 'token_counter', None)
        agent_name = getattr(self._app, 'current_agent_name', 'main') or 'main'

        # 构建完整的任务描述
        full_task = task
        if context:
            full_task = f"{task}\n\n补充上下文:\n{context}"

        is_web = getattr(self._app, 'is_web', False)

        # 获取上下文压缩组件
        context_compressor = getattr(self._app, 'context_compressor', None)
        context_window = getattr(self._app, 'context_window', None)
        auto_compress = True
        agent_config = getattr(self._app, 'current_agent_config', None)
        if agent_config:
            auto_compress = agent_config.auto_compress

        if is_web:
            return self._execute_serial_web(
                scheduler, agent_name, full_task, llm_client,
                tool_executor, token_counter,
                context_compressor, context_window, auto_compress)
        else:
            return self._execute_serial_cli(
                scheduler, agent_name, full_task, llm_client,
                tool_executor, token_counter,
                context_compressor, context_window, auto_compress)

    def _execute_serial_cli(self, scheduler, agent_name, full_task,
                            llm_client, tool_executor, token_counter,
                            context_compressor=None, context_window=None,
                            auto_compress=True) -> ToolResult:
        """CLI 模式：子agent直接实时输出到终端（原始行为）"""
        try:
            result = scheduler.delegate_and_run(
                parent_name=agent_name,
                task=full_task,
                model_config={},
                llm_client=llm_client,
                tool_executor=tool_executor,
                token_counter=token_counter,
                context_compressor=context_compressor,
                context_window=context_window,
                auto_compress=auto_compress
            )
            return ToolResult(success=True, output=result)
        except Exception as e:
            return ToolResult(
                success=False, output="",
                error=f"子Agent执行失败: {str(e)}"
            )

    def _execute_serial_web(self, scheduler, agent_name, full_task,
                            llm_client, tool_executor, token_counter,
                            context_compressor=None, context_window=None,
                            auto_compress=True) -> ToolResult:
        """Web 模式：捕获stdout，将子agent全部输出作为ToolResult返回前端"""
        captured = io.StringIO()
        old_stdout = sys.stdout

        # Web环境无交互终端，跳过确认
        if tool_executor:
            old_confirm = getattr(tool_executor, 'no_more_confirmations', False)
            tool_executor.no_more_confirmations = True

        try:
            sys.stdout = captured
            result = scheduler.delegate_and_run(
                parent_name=agent_name,
                task=full_task,
                model_config={},
                llm_client=llm_client,
                tool_executor=tool_executor,
                token_counter=token_counter,
                context_compressor=context_compressor,
                context_window=context_window,
                auto_compress=auto_compress
            )
        except Exception as e:
            return ToolResult(
                success=False, output="",
                error=f"子Agent执行失败: {str(e)}"
            )
        finally:
            sys.stdout = old_stdout
            if tool_executor:
                tool_executor.no_more_confirmations = old_confirm

        # 构建输出：子agent过程 + 最终结果
        transcript = captured.getvalue()
        transcript = _ANSI_RE.sub('', transcript).strip()

        output_parts = []
        if transcript:
            output_parts.append(f"[子Agent执行过程]\n{transcript}")
        output_parts.append(f"\n[子Agent最终结果]\n{result}")

        return ToolResult(success=True, output="\n".join(output_parts))

    # ==================================================================
    #  并行模式（多个独立子任务）
    # ==================================================================

    def _execute_parallel(self, tasks: list) -> ToolResult:
        """并行执行多个独立子任务，全部完成后汇总返回"""
        scheduler = getattr(self._app, 'subagent_scheduler', None)
        if not scheduler:
            return ToolResult(
                success=False, output="", error="子Agent调度器未初始化")

        llm_client = getattr(self._app, 'llm_client', None)
        if not llm_client:
            return ToolResult(
                success=False, output="", error="LLM客户端未初始化")

        if not isinstance(tasks, list) or not tasks:
            return ToolResult(
                success=False, output="", error="tasks 必须是非空列表")

        # 规范化任务描述
        full_tasks = []
        for i, item in enumerate(tasks):
            if isinstance(item, str):
                full_tasks.append(item)
                continue
            if not isinstance(item, dict) or not item.get("task"):
                return ToolResult(
                    success=False, output="",
                    error=f"第 {i + 1} 个子任务缺少 task 字段")
            full_task = item["task"]
            ctx = item.get("context", "")
            if ctx:
                full_task = f"{item['task']}\n\n补充上下文:\n{ctx}"
            full_tasks.append(full_task)

        if len(full_tasks) > MAX_TASKS:
            return ToolResult(
                success=False, output="",
                error=f"子任务数量 {len(full_tasks)} 超过上限 {MAX_TASKS}，请合并后重试")

        # 只有1个子任务时退化为串行（无需并行框架）
        if len(full_tasks) == 1:
            return self._execute_serial(full_tasks[0])

        tool_executor = getattr(self._app, 'tool_executor', None)
        token_counter = getattr(self._app, 'token_counter', None)
        agent_name = getattr(self._app, 'current_agent_name', 'main') or 'main'
        is_web = getattr(self._app, 'is_web', False)

        # 上下文压缩组件
        context_compressor = getattr(self._app, 'context_compressor', None)
        context_window = getattr(self._app, 'context_window', None)
        auto_compress = True
        agent_config = getattr(self._app, 'current_agent_config', None)
        if agent_config:
            auto_compress = agent_config.auto_compress

        # 并行期间自动确认工具调用（多线程无法安全进行交互式确认）
        old_confirm = None
        if tool_executor:
            old_confirm = getattr(tool_executor, 'no_more_confirmations', False)
            tool_executor.no_more_confirmations = True

        try:
            results = scheduler.run_parallel(
                parent_name=agent_name,
                tasks=full_tasks,
                llm_client=llm_client,
                tool_executor=tool_executor,
                token_counter=token_counter,
                context_compressor=context_compressor,
                context_window=context_window,
                auto_compress=auto_compress,
                live_status=not is_web,  # CLI实时状态板；Web端转录随结果返回前端
            )
        except Exception as e:
            return ToolResult(
                success=False, output="",
                error=f"并行子Agent执行失败: {str(e)}")
        finally:
            if tool_executor and old_confirm is not None:
                tool_executor.no_more_confirmations = old_confirm

        # 汇总结果
        ok_count = sum(1 for r in results if r["success"])
        parts = [f"[并行子Agent执行完成] 共 {len(results)} 个任务，成功 {ok_count} 个\n"]
        for r in results:
            icon = "✅" if r["success"] else "❌"
            parts.append(f"\n{'━' * 20} {icon} [{r['name']}] {'━' * 20}")
            parts.append(f"任务: {r['task']}")
            if is_web and r.get("transcript", "").strip():
                # Web端：附带执行过程转录（去ANSI控制序列）
                transcript = _ANSI_RE.sub('', r["transcript"]).strip()
                if transcript:
                    parts.append(f"\n[执行过程]\n{transcript}")
            parts.append(f"\n[结果]\n{r['result']}")

        return ToolResult(
            success=ok_count == len(results),
            output="\n".join(parts)
        )
