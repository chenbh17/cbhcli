"""Token计数器 - 估算文本token数量"""
import re
from typing import Optional


# 尝试导入tiktoken进行精确计数
try:
    import tiktoken
    HAS_TIKTOKEN = True
except ImportError:
    HAS_TIKTOKEN = False


class TokenCounter:
    """Token计数器,支持精确和估算两种模式"""
    
    def __init__(self, model_name: Optional[str] = None):
        """
        初始化Token计数器
        
        Args:
            model_name: 模型名称,用于选择合适的encoder
        """
        self.model_name = model_name
        self._encoder = None
        
        if HAS_TIKTOKEN and model_name:
            try:
                self._encoder = tiktoken.encoding_for_model(model_name)
            except KeyError:
                # 如果模型名称不被支持,使用cl100k_base (GPT-4/3.5)
                self._encoder = tiktoken.get_encoding("cl100k_base")
    
    def count_tokens(self, text: str) -> int:
        """
        计算文本的token数量
        
        Args:
            text: 输入文本
            
        Returns:
            token数量
        """
        if self._encoder:
            return len(self._encoder.encode(text))
        else:
            # 降级方案: 粗略估算 (英文约4字符/token, 中文约1.5字符/token)
            return self._estimate_tokens(text)
    
    def _estimate_tokens(self, text: str) -> int:
        """估算token数量(降级方案)"""
        # 统计中文字符
        chinese_chars = len(re.findall(r'[\u4e00-\u9fff]', text))
        # 统计其他字符
        other_chars = len(text) - chinese_chars
        
        # 中文约1.5字符/token, 英文约4字符/token
        return int(chinese_chars / 1.5 + other_chars / 4)
    
    def count_messages_tokens(self, messages: list[dict]) -> int:
        """
        计算消息列表的总token数
        
        Args:
            messages: 消息列表 [{role, content}]
            
        Returns:
            总token数
        """
        total = 0
        for msg in messages:
            # 每条消息有基础开销(约4 tokens用于role等元数据)
            total += 4
            total += self.count_tokens(msg.get("content", ""))
        return total


# 全局单例
_default_counter: Optional[TokenCounter] = None


def get_token_counter(model_name: Optional[str] = None) -> TokenCounter:
    """获取全局Token计数器实例"""
    global _default_counter
    if _default_counter is None:
        _default_counter = TokenCounter(model_name)
    return _default_counter
