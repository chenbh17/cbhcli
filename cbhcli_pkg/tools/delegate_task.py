"""任务委托工具 - 允许Agent将子任务委托给子Agent执行"""
import io
import re
import sys
from cbhcli_pkg.tools.registry import BaseTool, ToolResult


# ANSI escape code pattern
_ANSI_RE = re.compile(r'\x1b\[[0-9;]*m')


class DelegateTaskTool(BaseTool):
    """任务委托工具
    
    允许主Agent将独立的子任务委托给子Agent执行。
    子Agent拥有独立的会话上下文，共享主Agent的工具和LLM。
    适用于：可独立完成的子任务、不依赖当前对话上下文的操作。
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
            "将一个独立的子任务委托给子Agent执行。"
            "子Agent拥有独立上下文，适合处理不依赖当前对话历史的独立任务。"
            "例如：在一个文件中查找信息、执行一段独立的操作、生成一段独立的内容等。"
            "注意：子Agent无法访问当前对话历史，请在task中提供完整的任务描述。"
        )
    
    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "task": {
                    "type": "string",
                    "description": "子任务的完整描述，需包含足够的上下文信息让子Agent独立完成"
                },
                "context": {
                    "type": "string",
                    "description": "可选的额外上下文信息，如文件路径、目标要求等"
                }
            },
            "required": ["task"]
        }
    
    def execute(self, task: str, context: str = "", **kwargs) -> ToolResult:
        """执行任务委托
        
        Args:
            task: 子任务描述
            context: 额外上下文
            
        Returns:
            ToolResult: 子Agent的执行结果
        """
        scheduler = getattr(self._app, 'subagent_scheduler', None)
        if not scheduler:
            return ToolResult(
                success=False,
                output="",
                error="子Agent调度器未初始化"
            )
        
        llm_client = getattr(self._app, 'llm_client', None)
        if not llm_client:
            return ToolResult(
                success=False,
                output="",
                error="LLM客户端未初始化"
            )
        
        tool_executor = getattr(self._app, 'tool_executor', None)
        token_counter = getattr(self._app, 'token_counter', None)
        agent_name = getattr(self._app, 'current_agent_name', 'main') or 'main'
        
        # 构建完整的任务描述
        full_task = task
        if context:
            full_task = f"{task}\n\n补充上下文:\n{context}"
        
        is_web = getattr(self._app, 'is_web', False)
        
        if is_web:
            return self._execute_web(scheduler, agent_name, full_task,
                                     llm_client, tool_executor, token_counter)
        else:
            return self._execute_cli(scheduler, agent_name, full_task,
                                     llm_client, tool_executor, token_counter)
    
    def _execute_cli(self, scheduler, agent_name, full_task,
                     llm_client, tool_executor, token_counter) -> ToolResult:
        """CLI 模式：子agent直接实时输出到终端（原始行为）"""
        try:
            result = scheduler.delegate_and_run(
                parent_name=agent_name,
                task=full_task,
                model_config={},
                llm_client=llm_client,
                tool_executor=tool_executor,
                token_counter=token_counter
            )
            return ToolResult(success=True, output=result)
        except Exception as e:
            return ToolResult(
                success=False, output="",
                error=f"子Agent执行失败: {str(e)}"
            )
    
    def _execute_web(self, scheduler, agent_name, full_task,
                     llm_client, tool_executor, token_counter) -> ToolResult:
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
                token_counter=token_counter
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
