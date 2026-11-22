"""会话管理 - Session和ContextWindow"""
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional
import base64
import uuid


def _image_data_uri(b64: str) -> str:
    """裸 base64 → data URI，按魔数嗅探真实 MIME（不再硬编码 jpeg）。

    v5.5.2：PNG/GIF/WebP 等若被标成 image/jpeg，部分 API 会报
    "image format is illegal"。这里解码头部字节判断真实格式。
    兼容已带 data URI 前缀的输入（直接透传，避免二次包裹）。
    """
    if not b64:
        return b64
    if b64.startswith("data:"):
        return b64
    mime = "jpeg"
    try:
        head = base64.b64decode(b64[:32], validate=False)
        if head.startswith(b"\x89PNG\r\n\x1a\n"):
            mime = "png"
        elif head.startswith(b"\xff\xd8\xff"):
            mime = "jpeg"
        elif head.startswith((b"GIF87a", b"GIF89a")):
            mime = "gif"
        elif head[:4] == b"RIFF" and head[8:12] == b"WEBP":
            mime = "webp"
        elif head.startswith(b"BM"):
            mime = "bmp"
    except Exception:
        pass
    return f"data:image/{mime};base64,{b64}"


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
                    "image_url": {"url": _image_data_uri(img_data)}
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


class _DisplayMirrorList(list):
    """messages 列表：append/insert/extend 同步镜像到 display_messages。

    v5.5.1 迭代（压缩后显示不消失）的关键收口：CLI ai_handler 等处直接
    `session.messages.append(...)`（不走 add_message），用镜像列表保证
    **任何追加入口**都同步进显示层。整表替换（压缩 replace_messages /
    截断）只换上下文，不把新表内容复制进显示层——显示层保留压缩前对话。
    """

    def __init__(self, iterable=(), display=None):
        super().__init__(iterable)
        self._display = display

    def append(self, item):
        super().append(item)
        if self._display is not None:
            self._display.append(item)

    def insert(self, index, item):
        super().insert(index, item)
        if self._display is not None:
            self._display.insert(index, item)

    def extend(self, items):
        items = list(items)
        super().extend(items)
        if self._display is not None:
            self._display.extend(items)


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
        # 显示层消息（v5.5.1 迭代）：与 messages 同步追加，但**压缩不动它**。
        # 上下文压缩只替换 messages（发给模型的真实上下文）；界面/回显/
        # Web 恢复一律用 display_messages —— 压缩后对话显示不消失。
        self.display_messages = []
        self.messages = []
        self.tool_call_count: int = 0
        self.created_at = datetime.now()
        self.is_active = True

    # --- messages / display_messages 属性（镜像收口，见 _DisplayMirrorList）---
    @property
    def messages(self) -> list:
        return self._messages

    @messages.setter
    def messages(self, value):
        if isinstance(value, _DisplayMirrorList) \
                and value._display is self.display_messages:
            self._messages = value
        else:
            # 整表替换：初始内容**不**复制进显示层（压缩语义）
            self._messages = _DisplayMirrorList(value, self.display_messages)

    @property
    def display_messages(self) -> list:
        return self._display_messages

    @display_messages.setter
    def display_messages(self, value):
        self._display_messages = value
        # 重新绑定镜像列表的显示引用（防止 reset/resume 重绑后引用孤儿列表）
        m = getattr(self, "_messages", None)
        if isinstance(m, _DisplayMirrorList):
            m._display = value
    
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
        self.messages.append(msg)   # 镜像列表同步追加到 display_messages
        return msg

    def get_display_messages(self) -> list[dict]:
        """显示层消息（API 格式）：供保存/回显/Web 渲染，压缩后仍是完整对话"""
        return [msg.to_dict() for msg in self.display_messages]

    def set_display_messages(self, messages: list) -> None:
        """恢复会话时用落盘的显示层历史覆盖（dict 列表或 Message 列表）"""
        out: list[Message] = []
        for m in messages or []:
            if isinstance(m, Message):
                out.append(m)
            elif isinstance(m, dict):
                content = m.get("content") or ""
                if isinstance(content, list):
                    # 多模态格式兜底：取文本部分
                    content = " ".join(
                        p.get("text", "") for p in content
                        if isinstance(p, dict) and p.get("type") == "text")
                out.append(Message(
                    role=m.get("role", ""),
                    content=content,
                    tool_call_id=m.get("tool_call_id"),
                    tool_calls=m.get("tool_calls"),
                    reasoning_content=m.get("reasoning_content"),
                ))
        self.display_messages = out
    
    def get_context_messages(self) -> list[dict]:
        """
        获取用于API调用的上下文消息
        
        Returns:
            消息列表 [{role, content}]
        """
        return [msg.to_dict() for msg in self.messages]
    
    def get_total_tokens(self, token_counter=None) -> int:
        """获取会话总token数
        
        如果传入 token_counter，则使用 count_message_tokens 重新计算
        （含消息结构开销，更准确）；否则累加各消息存储的 token_count。
        
        Args:
            token_counter: TokenCounter 实例（可选，传入则精确计算）
            
        Returns:
            总token数
        """
        if token_counter is not None:
            return token_counter.count_messages_tokens(self.messages)
        return sum(msg.token_count for msg in self.messages)
    
    def reset(self) -> None:
        """清空会话,保留system消息"""
        system_messages = [msg for msg in self.messages if msg.role == "system"]
        self.messages = system_messages
        # 新会话语义：显示层一并清空（system 消息本来就不展示）
        self.display_messages = []
        self.tool_call_count = 0
    
    def remove_messages_from(self, index: int) -> None:
        """
        从指定索引开始删除消息
        
        Args:
            index: 起始索引
        """
        if 0 <= index < len(self.messages):
            self.messages = self.messages[:index]
        # 显示层不回退：已经展示给用户的内容保留（v5.5.1 迭代）
    
    def replace_messages(self, messages: list[Message]) -> None:
        """
        替换消息列表(用于上下文压缩)
        
        Args:
            messages: 新的消息列表

        注意（v5.5.1 迭代）：**只替换发给模型的真实上下文 messages，
        display_messages 保持不动**——压缩后界面仍显示压缩前的完整对话。
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

    def compression_target(self) -> int:
        """压缩后目标 token 数（默认窗口的 30%）

        与 trigger_threshold（80% 触发）不同：压缩目标应显著低于触发阈值，
        否则压缩后立即再次接近上限触发重复压缩。
        """
        from cbhcli_pkg.core.constants import COMPRESSION_TARGET_RATIO
        return int(self.model_limit * COMPRESSION_TARGET_RATIO)
    
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
