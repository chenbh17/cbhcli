"""LLM客户端 - 统一API调用封装"""
import requests
import json
from typing import Iterator, Optional

from cbhcli_pkg.core.constants import API_TIMEOUT


class LLMClient:
    """统一的LLM API客户端"""
    
    def __init__(self, model_config: dict):
        """
        初始化LLM客户端
        
        Args:
            model_config: 模型配置字典 {name, apiKey, url, model, context_limit}
        """
        self.base_url = model_config["url"].rstrip('/')
        self.api_key = model_config["apiKey"]
        self.model_name = model_config["model"]
        self.context_limit = model_config.get("context_limit", 128000)
        
        # 是否支持思考模式（动态检测：一旦模型返回 reasoning_content 就自动标记）
        self.supports_reasoning = False
        
        self._session = requests.Session()
        self._session.headers.update({
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        })
    
    def _clean_messages(self, messages: list[dict]) -> list[dict]:
        """清理消息，根据模型是否支持思考模式处理 reasoning_content 字段
        
        - supports_reasoning=True: 确保所有 assistant 消息都有 reasoning_content
          （旧历史消息可能缺失该字段，补为空字符串）
        - supports_reasoning=False: 剥离所有 reasoning_content 字段
        - 自动检测：消息历史中存在 reasoning_content 时，自动标记
        """
        # 自动检测：消息历史中存在 reasoning_content，说明模型支持思考模式
        if not self.supports_reasoning:
            if any(msg.get("reasoning_content") for msg in messages):
                self.supports_reasoning = True
        
        if self.supports_reasoning:
            # 思考模式：确保所有 assistant 消息都有 reasoning_content 字段
            # 旧版本保存的历史消息可能缺失该字段，DeepSeek 要求必须传回
            result = []
            for msg in messages:
                if msg.get("role") == "assistant" and "reasoning_content" not in msg:
                    msg = {**msg, "reasoning_content": ""}
                result.append(msg)
            return result
        
        # 非思考模式：剥离 reasoning_content
        cleaned = []
        for msg in messages:
            if "reasoning_content" in msg:
                msg = {k: v for k, v in msg.items() if k != "reasoning_content"}
            cleaned.append(msg)
        return cleaned
    
    def chat(self, messages: list[dict], temperature: float = 0.1, **kwargs) -> str:
        """
        非流式聊天完成
        
        Args:
            messages: 消息列表 [{role, content}]
            temperature: 温度参数
            **kwargs: 其他参数
            
        Returns:
            AI响应文本
        """
        payload = {
            "model": self.model_name,
            "messages": self._clean_messages(messages),
            "temperature": temperature,
            **kwargs
        }
        
        response = self._session.post(
            f"{self.base_url}/chat/completions",
            json=payload,
            timeout=API_TIMEOUT
        )
        
        # 自动检测思考模式：400 + reasoning_content 错误时标记并重试
        if response.status_code == 400 and not self.supports_reasoning:
            if "reasoning_content" in response.text:
                self.supports_reasoning = True
                payload["messages"] = self._clean_messages(messages)
                response = self._session.post(
                    f"{self.base_url}/chat/completions",
                    json=payload,
                    timeout=API_TIMEOUT
                )
        
        if response.status_code != 200:
            raise Exception(f"API请求失败: {response.status_code} - {response.text}")
        
        result = response.json()
        return result["choices"][0]["message"]["content"].strip()
    
    def chat_stream(self, messages: list[dict], temperature: float = 0.1, **kwargs) -> Iterator[tuple[str, str]]:
        """
        流式聊天完成
        
        Args:
            messages: 消息列表 [{role, content}]
            temperature: 温度参数
            **kwargs: 其他参数
            
        Yields:
            元组 (类型, 内容):
            - ("reasoning", content): 思考过程
            - ("content", content): 正常回答内容
            - ("tool_calls", json_str): 工具调用（JSON字符串）
        """
        payload = {
            "model": self.model_name,
            "messages": self._clean_messages(messages),
            "temperature": temperature,
            "stream": True,
            **kwargs
        }
        
        response = self._session.post(
            f"{self.base_url}/chat/completions",
            json=payload,
            stream=True,
            timeout=API_TIMEOUT
        )
        
        # 自动检测思考模式：400 + reasoning_content 错误时标记并重试
        if response.status_code == 400 and not self.supports_reasoning:
            error_text = response.text
            if "reasoning_content" in error_text:
                self.supports_reasoning = True
                payload["messages"] = self._clean_messages(messages)
                response = self._session.post(
                    f"{self.base_url}/chat/completions",
                    json=payload,
                    stream=True,
                    timeout=API_TIMEOUT
                )
        
        if response.status_code != 200:
            raise Exception(f"API请求失败: {response.status_code} - {response.text}")
        
        for line in response.iter_lines():
            if line:
                line_str = line.decode('utf-8')
                if line_str.startswith('data: '):
                    data_str = line_str[6:]
                    if data_str.strip() == '[DONE]':
                        break
                    try:
                        chunk = json.loads(data_str)
                        if 'choices' in chunk and len(chunk['choices']) > 0:
                            delta = chunk['choices'][0].get('delta', {})
                            
                            # 处理思考模型的 reasoning_content 字段
                            reasoning_delta = delta.get('reasoning_content') or delta.get('reasoning')
                            if reasoning_delta:
                                # 动态标记：模型返回了 reasoning_content，后续请求需传回
                                self.supports_reasoning = True
                                yield ("reasoning", reasoning_delta)
                                continue
                            
                            # 处理工具调用（某些模型通过 tool_calls 字段返回）
                            if delta.get('tool_calls'):
                                yield ("tool_calls", json.dumps(delta['tool_calls']))
                                continue
                            
                            content = delta.get('content', '')
                            if content:
                                yield ("content", content)
                    except json.JSONDecodeError:
                        continue
    
    def embeddings(self, texts: list[str]) -> list[list[float]]:
        """
        获取文本的embedding向量
        
        Args:
            texts: 文本列表
            
        Returns:
            embedding向量列表
        """
        payload = {
            "model": self.model_name,
            "input": texts
        }
        
        response = self._session.post(
            f"{self.base_url}/embeddings",
            json=payload,
            timeout=API_TIMEOUT
        )
        
        if response.status_code != 200:
            raise Exception(f"Embedding请求失败: {response.status_code} - {response.text}")
        
        result = response.json()
        return [item["embedding"] for item in result["data"]]
