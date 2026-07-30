"""call_agent 工具 - 链条中上游 Agent 调用下游用户 Agent"""
from cbhcli_pkg.tools.registry import BaseTool, ToolResult


class CallAgentTool(BaseTool):
    """调用 Agent 链条中的下游 Agent 执行任务

    仅在会话绑定了 Agent 链条时注册此工具。
    下游 Agent 以自己的完整身份（系统提示、工具、工作空间、记忆、技能）执行任务。
    """

    def __init__(self, app, chain, current_agent: str):
        """
        Args:
            app: CBHCLIApp 实例
            chain: 当前激活的 AgentChain
            current_agent: 当前 Agent 名称（调用方）
        """
        self._app = app
        self._chain = chain
        self._current_agent = current_agent

    @property
    def name(self) -> str:
        return "call_agent"

    @property
    def description(self) -> str:
        downstream = self._chain.get_downstream_agents(self._current_agent)
        downstream_str = ", ".join(downstream) if downstream else "(无)"
        return (
            "调用 Agent 链条中的下游用户 Agent 执行任务。"
            f"当前可调用的下游 Agent: {downstream_str}。"
            "下游 Agent 会以自己的完整身份（系统提示、工具、工作空间、记忆、技能、MCP）执行任务，"
            "完成后将结果回传给你。"
            "同级多个下游 Agent 可在同一次回复中多次调用 call_agent 实现并行执行。"
            "注意：下游 Agent 是用户创建的持久化 Agent，拥有独立工作空间，不是临时子 Agent。"
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "agent_name": {
                    "type": "string",
                    "description": "要调用的下游 Agent 名称（必须是当前链条中你的合法下游）"
                },
                "task": {
                    "type": "string",
                    "description": "交给下游 Agent 的任务描述，需包含足够的上下文信息让下游 Agent 独立完成"
                }
            },
            "required": ["agent_name", "task"]
        }

    def execute(self, agent_name: str = "", task: str = "", **kwargs) -> ToolResult:
        """执行下游 Agent 调用

        Args:
            agent_name: 目标下游 Agent 名称
            task: 任务描述

        Returns:
            ToolResult: 下游 Agent 的执行结果
        """
        if not agent_name:
            return ToolResult(
                success=False,
                output="",
                error="缺少参数: agent_name"
            )
        if not task:
            return ToolResult(
                success=False,
                output="",
                error="缺少参数: task"
            )

        # 校验是否为合法下游
        if not self._chain.is_valid_downstream(self._current_agent, agent_name):
            valid = self._chain.get_downstream_agents(self._current_agent)
            return ToolResult(
                success=False,
                output="",
                error=(
                    f"'{agent_name}' 不是当前 Agent '{self._current_agent}' 的合法下游 Agent。"
                    f"可调用的下游 Agent: {', '.join(valid) if valid else '(无)'}"
                )
            )

        from cbhcli_pkg.core.agent_chain import ChainExecutor
        from cbhcli_pkg.core.constants import (
            C_DIM, C_RESET, C_SUBAGENT_HINT, C_SUBAGENT_TEXT
        )

        # 获取下游 Agent 所在层级
        target_level = self._chain.get_agent_level(agent_name)
        print(f"\n{C_SUBAGENT_HINT}📌 调用 Agent: {agent_name} (Level {target_level}){C_RESET}")
        print(f"{C_DIM}   任务: {task[:200]}{'...' if len(task) > 200 else ''}{C_RESET}")

        # 更新状态栏路径（如果 app 支持）
        if hasattr(self._app, '_chain_active_path'):
            old_path = self._app._chain_active_path
            self._app._chain_active_path = old_path + [agent_name]

        try:
            executor = ChainExecutor(self._app)
            result = executor.execute(
                chain=self._chain,
                upstream_agent=self._current_agent,
                target_agent=agent_name,
                task=task,
            )

            print(f"\n{C_SUBAGENT_HINT}✅ {agent_name} 完成{C_RESET}")
            return ToolResult(
                success=True,
                output=result,
            )
        except Exception as e:
            print(f"\n{C_SUBAGENT_HINT}❌ {agent_name} 执行失败: {e}{C_RESET}")
            return ToolResult(
                success=False,
                output="",
                error=f"下游 Agent '{agent_name}' 执行失败: {str(e)}"
            )
        finally:
            # 恢复状态栏路径
            if hasattr(self._app, '_chain_active_path'):
                self._app._chain_active_path = old_path
