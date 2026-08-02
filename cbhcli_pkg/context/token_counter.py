"""Token计数器 - 估算文本token数量"""
import json
import re
from typing import Optional


# 尝试导入tiktoken进行精确计数
try:
    import tiktoken
    HAS_TIKTOKEN = True
except ImportError:
    HAS_TIKTOKEN = False


# 每条消息的结构开销（role 标记、分隔符等，参考 OpenAI 官方估算）
# https://github.com/openai/openai-cookbook/blob/main/examples/How_to_count_tokens_with_tiktoken.ipynb
MESSAGE_OVERHEAD = 4        # 每条消息基础开销: <im_start>{role/name}\n{content}<im_end>\n
TOOL_CALL_OVERHEAD = 3       # tool_calls JSON 结构外壳开销
TOOL_CALL_ID_OVERHEAD = 3    # tool_call_id 字段开销
REPLY_OVERHEAD = 3           # 对话回复前缀开销


class TokenCounter:
    """Token计数器,支持精确和估算两种模式
    
    精确模式: 使用 tiktoken (cl100k_base 编码) 计数
    估算模式: tiktoken 不可用时,基于字符特征的粗略估算
    """
    
    def __init__(self, model_name: Optional[str] = None):
        """
        初始化Token计数器
        
        Args:
            model_name: 模型名称,用于选择合适的encoder
        """
        self.model_name = model_name
        self._encoder = None
        
        if HAS_TIKTOKEN:
            if model_name:
                try:
                    self._encoder = tiktoken.encoding_for_model(model_name)
                except KeyError:
                    # 模型名称不被 tiktoken 支持（如国产模型），降级到 cl100k_base
                    self._encoder = tiktoken.get_encoding("cl100k_base")
            else:
                # 没有模型名称也用 cl100k_base，比估算准确得多
                self._encoder = tiktoken.get_encoding("cl100k_base")
    
    def count_tokens(self, text: str) -> int:
        """
        计算文本的token数量
        
        Args:
            text: 输入文本
            
        Returns:
            token数量
        """
        if not text:
            return 0
            
        if self._encoder:
            return len(self._encoder.encode(text))
        else:
            # 降级方案: 粗略估算
            return self._estimate_tokens(text)
    
    def _estimate_tokens(self, text: str) -> int:
        """估算token数量(降级方案,无tiktoken时使用)"""
        if not text:
            return 0
        # 统计中文字符
        chinese_chars = len(re.findall(r'[\u4e00-\u9fff]', text))
        # 统计其他字符
        other_chars = len(text) - chinese_chars
        
        # 中文约1.5字符/token, 英文约4字符/token
        return int(chinese_chars / 1.5 + other_chars / 4)
    
    def count_message_tokens(self, msg) -> int:
        """计算单条消息的token数（含结构开销）
        
        比单纯 count_tokens(content) 更准确，包含了：
        - 每条消息的 role 标记开销
        - tool_calls 的 JSON 结构开销
        - tool_call_id 的开销
        - reasoning_content 的开销
        
        Args:
            msg: Message 对象或 dict（支持两种格式）
            
        Returns:
            消息的总token数（含结构开销）
        """
        # 兼容 Message 对象和 dict
        if hasattr(msg, 'role'):
            role = msg.role
            content = msg.content or ""
            tool_calls = msg.tool_calls
            tool_call_id = msg.tool_call_id
            reasoning_content = getattr(msg, 'reasoning_content', None)
        else:
            role = msg.get("role", "")
            content = msg.get("content", "") or ""
            tool_calls = msg.get("tool_calls")
            tool_call_id = msg.get("tool_call_id")
            reasoning_content = msg.get("reasoning_content")
        
        total = MESSAGE_OVERHEAD  # 每条消息基础开销
        
        # content token
        total += self.count_tokens(content)
        
        # tool_calls 结构开销
        if tool_calls:
            total += TOOL_CALL_OVERHEAD
            total += self.count_tokens(json.dumps(tool_calls, ensure_ascii=False))
        
        # tool_call_id 开销
        if tool_call_id:
            total += TOOL_CALL_ID_OVERHEAD
            total += self.count_tokens(tool_call_id)
        
        # reasoning_content 开销（DeepSeek 等思考模型）
        if reasoning_content:
            total += self.count_tokens(reasoning_content)
        
        return total
    
    def count_messages_tokens(self, messages: list) -> int:
        """
        计算消息列表的总token数（含每条消息的结构开销）
        
        Args:
            messages: 消息列表（Message 对象或 dict）
            
        Returns:
            总token数
        """
        total = REPLY_OVERHEAD  # 对话回复前缀
        for msg in messages:
            total += self.count_message_tokens(msg)
        return total


# 全局单例
_default_counter: Optional[TokenCounter] = None


def get_token_counter(model_name: Optional[str] = None) -> TokenCounter:
    """获取全局Token计数器实例
    
    修复：如果传入了 model_name 且与当前单例不同，则重新初始化，
    避免第一次无 model_name 调用导致永远走估算模式的问题。
    
    Args:
        model_name: 模型名称（可选）。传入时会更新单例的 encoder。
        
    Returns:
        TokenCounter 实例
    """
    global _default_counter
    if _default_counter is None:
        _default_counter = TokenCounter(model_name)
    elif model_name and _default_counter.model_name != model_name:
        # 模型名称变化时重新初始化（修复单例陷阱）
        _default_counter = TokenCounter(model_name)
    return _default_counter