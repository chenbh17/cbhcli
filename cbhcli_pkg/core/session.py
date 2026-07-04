"""会话管理 - Session和ContextWindow"""
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional
import uuid


@dataclass
class Message:
    """消息数据结构"""
    role: str  # "system" | "user" | "assistant" | "tool"
    content: str
    token_count: int = 0
    timestamp: datetime = field(default_factory=datetime.now)
    metadata: Optional[dict] = None
    tool_call_id: Optional[str] = None  # tool 消息关联的 tool_call ID
    tool_calls: Optional[list] = None  # assistant 消息的工具调用信息
    reasoning_content: Optional[str] = None  # DeepSeek 等思考模型的推理内容
    images: Optional[list] = None  # 图片列表（base64编码）

    def to_dict(self) -> dict:
        """转换为API消息格式"""
        msg = {"role": self.role}

        if self.role == "assistant" and self.tool_calls:
            # assistant 使用工具调用时，需要 tool_calls 字段
            msg["tool_calls"] = self.tool_calls
            msg["content"] = self.content if self.content else None
        elif self.role == "user" and self.images:
            # 用户消息包含图片时，使用多模态格式
            content_parts = [{"type": "text", "text": self.content}]
            for img_data in self.images:
                content_parts.append({
                    "type": "image_url",
                    "image_url": {"url": f"data:image/jpeg;base64,{img_data}"}
                })
            msg["content"] = content_parts
        else:
            msg["content"] = self.content

        if self.role == "tool" and self.tool_call_id:
            # tool 消息必须包含 tool_call_id
            msg["tool_call_id"] = self.tool_call_id

        # DeepSeek 思考模式要求将 reasoning_content 传回 API
        if self.role == "assistant" and self.reasoning_content:
            msg["reasoning_content"] = self.reasoning_content

        return msg


class Session:
    """会话管理"""
    
    def __init__(self, agent_name: str = ""):
        """
        初始化会话
        
        Args:
            agent_name: 关联的Agent名称
        """
        self.id = str(uuid.uuid4())
        self.agent_name = agent_name
        self.messages: list[Message] = []
        self.tool_call_count: int = 0
        self.created_at = datetime.now()
        self.is_active = True
    
    def add_message(self, role: str, content: str, token_count: int = 0, 
                    metadata: Optional[dict] = None, 
                    tool_call_id: Optional[str] = None,
                    tool_calls: Optional[list] = None,
                    reasoning_content: Optional[str] = None,
                    images: Optional[list] = None) -> Message:
        """
        添加消息到会话
        
        Args:
            role: 消息角色
            content: 消息内容
            token_count: token数量(0表示需要计算)
            metadata: 额外元数据
            tool_call_id: tool 消息关联的 tool_call ID
            tool_calls: assistant 消息的工具调用信息
            reasoning_content: 思考模型的推理内容
            
        Returns:
            Message: 添加的消息对象
        """
        msg = Message(
            role=role,
            content=content,
            token_count=token_count,
            metadata=metadata,
            tool_call_id=tool_call_id,
            tool_calls=tool_calls,
            reasoning_content=reasoning_content,
            images=images
        )
        self.messages.append(msg)
        return msg
    
    def get_context_messages(self) -> list[dict]:
        """
        获取用于API调用的上下文消息
        
        Returns:
            消息列表 [{role, content}]
        """
        return [msg.to_dict() for msg in self.messages]
    
    def get_total_tokens(self) -> int:
        """
        获取会话总token数
        
        Returns:
            总token数
        """
        return sum(msg.token_count for msg in self.messages)
    
    def reset(self) -> None:
        """清空会话,保留system消息"""
        system_messages = [msg for msg in self.messages if msg.role == "system"]
        self.messages = system_messages
        self.tool_call_count = 0
    
    def remove_messages_from(self, index: int) -> None:
        """
        从指定索引开始删除消息
        
        Args:
            index: 起始索引
        """
        if 0 <= index < len(self.messages):
            self.messages = self.messages[:index]
    
    def replace_messages(self, messages: list[Message]) -> None:
        """
        替换消息列表(用于上下文压缩)
        
        Args:
            messages: 新的消息列表
        """
        self.messages = messages


class ContextWindow:
    """上下文窗口管理"""
    
    def __init__(self, model_limit: int, compression_ratio: float = 0.8,
                 tools_schema_tokens: int = 0):
        """
        初始化上下文窗口
        
        Args:
            model_limit: 模型最大token数
            compression_ratio: 触发压缩的阈值比例(默认80%)
            tools_schema_tokens: OpenAI tools schema 占用的 token 数
        """
        self.model_limit = model_limit
        self.compression_ratio = compression_ratio
        self.tools_schema_tokens = tools_schema_tokens
        self.current_usage = 0
    
    def update(self, token_count: int) -> None:
        """
        更新当前token使用量
        
        Args:
            token_count: 会话消息的总token数（不含 tools schema）
        """
        self.current_usage = token_count + self.tools_schema_tokens
    
    def usage_percentage(self) -> float:
        """
        获取当前使用百分比
        
        Returns:
            使用百分比 (0.0 - 1.0)
        """
        if self.model_limit == 0:
            return 0.0
        return self.current_usage / self.model_limit
    
    def is_near_limit(self) -> bool:
        """是否接近上限"""
        return self.usage_percentage() >= self.compression_ratio
    
    def needs_compression(self) -> bool:
        """是否需要压缩"""
        return self.is_near_limit()
    
    def trigger_threshold(self) -> int:
        """获取触发压缩的token阈值"""
        return int(self.model_limit * self.compression_ratio)
    
    def remaining_tokens(self) -> int:
        """获取剩余可用token数"""
        return max(0, self.model_limit - self.current_usage)
    
    def get_status_text(self) -> str:
        """
        获取上下文状态文本
        
        Returns:
            状态文本
        """
        percentage = self.usage_percentage() * 100
        msg_tokens = self.current_usage - self.tools_schema_tokens
        parts = f"上下文使用: {self.current_usage:,} / {self.model_limit:,} tokens ({percentage:.1f}%)"
        if self.tools_schema_tokens > 0:
            parts += f"\n  消息: {msg_tokens:,} + tools schema: {self.tools_schema_tokens:,}"
        return parts
