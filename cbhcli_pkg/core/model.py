"""LLM客户端 - 统一API调用封装"""
import requests
import json
import signal
from typing import Iterator, Optional

from cbhcli_pkg.core.constants import API_TIMEOUT, SUMMARY_MAX_TOKENS


def _ensure_sigint_handler():
    """确保 SIGINT 由 Python 默认处理器接管（Ctrl+C 抛 KeyboardInterrupt）。

    某些依赖（如 chromadb 的 Rust/Tokio 内核）启动后台运行时会把 SIGINT
    置为 SIG_IGN，导致流式响应期间 Ctrl+C 无法中断。这里在每次阻塞读取前
    重新声明默认处理器，保证可中断。
    """
    try:
        if signal.getsignal(signal.SIGINT) is signal.SIG_IGN:
            signal.signal(signal.SIGINT, signal.default_int_handler)
    except Exception:
        pass


def _is_thinking_disabled(value) -> bool:
    """判断 thinking 参数值是否表示"禁用思考"（兼容 布尔/dict/字符串 形式）

    v5.2.3：调用方（如上下文压缩摘要）可通过 kwargs 显式传入 thinking=disabled
    覆盖模型配置。此时必须同步移除 reasoning_effort——DeepSeek 等 API 在
    thinking disabled 时携带 reasoning_effort 会报 400
    ("thinking options type cannot be disabled when reasoning_effort...")。
    """
    if value is False:
        return True
    if isinstance(value, dict):
        return value.get("type") == "disabled"
    if isinstance(value, str):
        return value.strip().lower() in ("false", "off", "0", "no", "n", "disabled")
    return False


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
        # 模型专属温度配置（可选，未设置则使用调用时传入的值或默认值）
        self.model_temperature = model_config.get("temperature")
        
        # 是否支持视觉（图片输入）
        self.supports_vision = model_config.get("vision", False)
        
        # 最大输出 token 数（可选，未设置则不传给 API，使用 API 默认值）
        self.max_tokens = model_config.get("max_tokens")
        
        # 思考模式参数（可选，None 则不传给 API，如 true/false）
        # 注意：这里的 thinking 是 API 请求参数（如 DeepSeek thinking: true/false），
        # 与 supports_reasoning（动态检测的推理模式标记）相互独立
        self.thinking = model_config.get("thinking")
        
        # 推理强度参数（可选，None 则不传给 API，如 low/medium/high）
        # 注意：DeepSeek 等 API 在 thinking=disabled 时不允许传 reasoning_effort
        # （400: thinking options type cannot be disabled when reasoning_effort...），
        # 发送时由 _get_extra_payload 统一处理
        self.reasoning_effort = model_config.get("reasoning_effort")
        
        # 是否支持思考模式（动态检测：一旦模型返回 reasoning_content 就自动标记）
        self.supports_reasoning = False
        
        self._session = requests.Session()
        self._session.headers.update({
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        })
    
    def _clean_messages(self, messages: list[dict]) -> list[dict]:
        """清理消息，根据模型能力处理特殊字段

        - 非视觉模型：多模态消息降级为纯文本（图片替换为占位说明），
          避免 fallback 到非视觉模型时 API 报"不是视觉模型"错误
        - supports_reasoning=True: 确保所有 assistant 消息都有 reasoning_content
          （旧历史消息可能缺失该字段，补为空字符串）
        - supports_reasoning=False: 剥离所有 reasoning_content 字段
        - 自动检测：消息历史中存在 reasoning_content 时，自动标记
        """
        # 非视觉模型：剥除图片内容（视觉主模型 fallback 到非视觉模型时，
        # 会话历史中的带图消息原样发送会被 API 拒绝）
        if not self.supports_vision:
            messages = [self._strip_images(msg) for msg in messages]

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

    @staticmethod
    def _strip_images(msg: dict) -> dict:
        """将单条多模态消息降级为纯文本（图片替换为占位说明）

        content 为字符串时原样返回；为列表（多模态格式）时，
        保留文本部分，image_url 部分替换为 "[已省略 N 张图片]"。
        """
        content = msg.get("content")
        if not isinstance(content, list):
            return msg

        texts = []
        img_count = 0
        for part in content:
            if isinstance(part, dict):
                if part.get("type") == "text":
                    texts.append(part.get("text", ""))
                elif part.get("type") == "image_url":
                    img_count += 1
        if img_count:
            texts.append(f"[已省略 {img_count} 张图片：当前模型不支持视觉]")
        return {**msg, "content": "\n".join(t for t in texts if t)}
    
    def _get_temperature(self, temperature: float) -> float:
        """获取温度参数：优先使用模型配置，否则使用传入值"""
        if self.model_temperature is not None:
            return self.model_temperature
        return temperature

    @staticmethod
    def _normalize_thinking(value):
        """将 thinking 配置规范化为 API 期望的 ThinkingOptions 结构体

        DeepSeek 等 API 期望 {"type": "enabled"} / {"type": "disabled"} 结构体，
        不接受布尔值（否则 400: thinking: invalid type: boolean ...）。
        """
        if isinstance(value, dict):
            return value
        if isinstance(value, bool):
            return {"type": "enabled" if value else "disabled"}
        if isinstance(value, str):
            v = value.strip().lower()
            if v in ("true", "on", "1", "yes", "y", "enabled"):
                return {"type": "enabled"}
            if v in ("false", "off", "0", "no", "n", "disabled"):
                return {"type": "disabled"}
        return value  # 无法识别的值原样传（保险，不拦截）

    def _get_extra_payload(self) -> dict:
        """获取思考相关参数（None 不传给 API）

        - thinking: 规范化为 {"type": "enabled"/"disabled"} 结构体
        - thinking=False 时忽略 reasoning_effort（DeepSeek 等 API
          不允许 thinking disabled 时携带 reasoning_effort，会报 400）
        """
        extra = {}
        if self.thinking is not None:
            extra["thinking"] = self._normalize_thinking(self.thinking)
        if self.reasoning_effort is not None and self.thinking is not False:
            extra["reasoning_effort"] = self.reasoning_effort
        return extra

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
        # v5.2.4：skip_thinking=True 表示本次请求完全不携带 thinking/reasoning_effort。
        # 调用方哨兵参数，先 pop 出 kwargs 防止混入 payload。用于压缩摘要请求：
        # 部分 API 不支持 thinking.type=disabled（400），摘要这类简单请求直接
        # 不带这两个参数对所有模型最兼容。
        skip_thinking = kwargs.pop("skip_thinking", False)
        payload = {
            "model": self.model_name,
            "messages": self._clean_messages(messages),
            "temperature": self._get_temperature(temperature),
            **self._get_extra_payload(),
            **kwargs
        }
        if self.max_tokens and "max_tokens" not in kwargs:
            payload["max_tokens"] = self.max_tokens
        if skip_thinking:
            payload.pop("thinking", None)
            payload.pop("reasoning_effort", None)
        # v5.2.3：调用方显式覆盖 thinking 为 disabled（如压缩摘要请求）时，
        # 移除与之冲突的 reasoning_effort（DeepSeek 400: thinking disabled 时不能携带）
        if "thinking" in kwargs and _is_thinking_disabled(kwargs["thinking"]):
            payload.pop("reasoning_effort", None)
        
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

        def _extract_content(r: dict):
            """安全提取 (content, reasoning, finish_reason)。"""
            choice = (r.get("choices") or [{}])[0]
            message = choice.get("message") or {}
            content = message.get("content") or ""
            if not isinstance(content, str):
                content = str(content)
            reasoning = (message.get("reasoning_content")
                         or message.get("reasoning") or "") or ""
            return content, reasoning, choice.get("finish_reason") or ""

        # v5.3.0：思考模型（qwen3 等）思考阶段耗尽 max_tokens 时 API 返回
        # content=null（内容全在 reasoning_content），旧代码直接对 None 调
        # .strip() 抛 'NoneType' object has no attribute 'strip'。修复：
        # 安全提取 + 空正文诊断 + finish_reason=length 时翻倍预算重试一次。
        content, reasoning, finish = _extract_content(result)
        if not content.strip() and finish == "length" \
                and payload.get("max_tokens"):
            # 截断发生在思考阶段：翻倍 max_tokens 重试一次（封顶 SUMMARY_MAX_TOKENS）
            retry_max = min(int(payload["max_tokens"]) * 2, SUMMARY_MAX_TOKENS)
            if retry_max > int(payload["max_tokens"]):
                response = self._session.post(
                    f"{self.base_url}/chat/completions",
                    json={**payload, "max_tokens": retry_max},
                    timeout=API_TIMEOUT
                )
                if response.status_code != 200:
                    raise Exception(
                        f"API请求失败: {response.status_code} - {response.text}")
                content, reasoning, finish = _extract_content(response.json())

        if not content.strip():
            req_max = payload.get("max_tokens")
            detail = (f"思考内容 {len(reasoning)} 字符" if reasoning
                      else "无思考内容")
            hint = ("，思考可能耗尽输出预算" if reasoning and finish == "length"
                    else "")
            raise Exception(
                f"模型未返回正文内容（finish_reason={finish or 'unknown'}，"
                f"{detail}，max_tokens={req_max if req_max is not None else '未设置'}"
                f"{hint}）。建议调大该模型的 max_tokens 配置或关闭其思考模式")
        return content.strip()
    
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
        # v5.2.4：skip_thinking 哨兵（同 chat()，见上方注释）
        skip_thinking = kwargs.pop("skip_thinking", False)
        payload = {
            "model": self.model_name,
            "messages": self._clean_messages(messages),
            "temperature": self._get_temperature(temperature),
            "stream": True,
            **self._get_extra_payload(),
            **kwargs
        }
        # 调用方显式传入的 max_tokens（kwargs）优先于模型配置
        if self.max_tokens and "max_tokens" not in kwargs:
            payload["max_tokens"] = self.max_tokens
        if skip_thinking:
            payload.pop("thinking", None)
            payload.pop("reasoning_effort", None)
        # v5.2.3：调用方显式覆盖 thinking 为 disabled 时，移除冲突的 reasoning_effort
        if "thinking" in kwargs and _is_thinking_disabled(kwargs["thinking"]):
            payload.pop("reasoning_effort", None)
        
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
        
        # 阻塞读取前确保 Ctrl+C 可中断（对抗 Rust 运行时设置的 SIG_IGN）
        _ensure_sigint_handler()
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
