"""上下文压缩器 - AI驱动的上下文压缩"""
from cbhcli_pkg.core.session import Session, Message
from cbhcli_pkg.core.model import LLMClient
from cbhcli_pkg.context.token_counter import TokenCounter


class ContextCompressor:
    """上下文压缩器"""
    
    def __init__(self, llm_client: LLMClient, token_counter: TokenCounter):
        """
        初始化上下文压缩器
        
        Args:
            llm_client: LLM客户端
            token_counter: Token计数器
        """
        self.llm_client = llm_client
        self.token_counter = token_counter
    
    def compress(self, session: Session, target_tokens: int) -> bool:
        """
        压缩会话上下文到目标token数
        
        Args:
            session: 会话对象
            target_tokens: 目标token数
            
        Returns:
            是否成功压缩
        """
        # 提取system消息(保留)
        system_messages = [msg for msg in session.messages if msg.role == "system"]
        
        # 提取user和assistant消息
        conversation_messages = [msg for msg in session.messages 
                                if msg.role in ["user", "assistant", "tool"]]
        
        if len(conversation_messages) <= 6:
            # 消息太少,不需要压缩
            return False
        
        # 保留最早的2轮和最近的3轮
        early_messages = conversation_messages[:4]  # 最早2轮(user+assistant)
        recent_messages = conversation_messages[-6:]  # 最近3轮
        
        # 中间部分需要压缩
        middle_messages = conversation_messages[4:-6]
        
        if not middle_messages:
            return False
        
        # 生成中间部分的摘要
        middle_text = "\n".join([
            f"{msg.role}: {msg.content}" 
            for msg in middle_messages
        ])
        
        summary = self._generate_summary(middle_text)
        
        # 构建新的消息列表
        new_messages = system_messages.copy()
        
        # 添加最早的消息
        new_messages.extend(early_messages)
        
        # 添加摘要
        summary_msg = Message(
            role="system",
            content=f"[历史对话摘要]\n{summary}",
            token_count=self.token_counter.count_tokens(summary) + 20
        )
        new_messages.append(summary_msg)
        
        # 添加最近的消息
        new_messages.extend(recent_messages)
        
        # 替换会话消息
        session.replace_messages(new_messages)
        
        return True
    
    def _generate_summary(self, text: str) -> str:
        """
        生成对话摘要
        
        Args:
            text: 对话文本
            
        Returns:
            摘要文本
        """
        system_prompt = """你是一个对话摘要专家。请总结以下对话的关键信息:
- 用户的需求和目标
- 执行的重要操作
- 修改的文件和内容
- 遇到的问题和解决方案
- 当前的任务状态

请保持简洁,但要保留所有重要的技术细节(如文件路径、命令、错误信息等)。"""

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"请总结以下对话:\n\n{text}"}
        ]
        
        try:
            summary = self.llm_client.chat(messages, temperature=0.3)
            return summary
        except Exception as e:
            return f"[压缩失败: {str(e)}]\n\n{text[:500]}..."
