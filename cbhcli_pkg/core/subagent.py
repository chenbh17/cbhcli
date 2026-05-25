"""子Agent机制 - 临时子Agent创建、管理和执行"""
import sys
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


class SubAgentScheduler:
    """子Agent调度器 - 支持任务分发和独立执行"""
    
    def __init__(self):
        self._active_subagents: dict[str, SubAgent] = {}
    
    def spawn(self, parent_name: str, task: str, model_config: dict) -> SubAgent:
        """
        创建子Agent
        
        Args:
            parent_name: 父Agent名称
            task: 任务描述
            model_config: 模型配置
            
        Returns:
            SubAgent实例
        """
        name = f"subagent_{len(self._active_subagents) + 1}"
        
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
        system_prompt: str = ""
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
            
            result = handler.process_request(sub_agent.task)
            sub_agent.complete(result)
            
            print(f"\n{C_SUBAGENT_HINT}[SubAgent:{sub_agent.name}] 任务完成{C_RESET}")
            
        except Exception as e:
            error_msg = str(e)
            sub_agent.fail(error_msg)
            result = f"子Agent执行失败: {error_msg}"
            print(f"\n{C_ERROR}[SubAgent:{sub_agent.name}] 执行失败: {error_msg}{C_RESET}")
        
        return result
    
    def delegate_and_run(
        self,
        parent_name: str,
        task: str,
        model_config: dict,
        llm_client: 'LLMClient',
        tool_executor: 'ToolExecutor',
        token_counter: 'TokenCounter',
        system_prompt: str = ""
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
            
        Returns:
            子Agent执行结果
        """
        sub_agent = self.spawn(parent_name, task, model_config)
        result = self.run(sub_agent, llm_client, tool_executor, token_counter, system_prompt)
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
