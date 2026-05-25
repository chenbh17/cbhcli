"""AI请求处理器 - 纯 OpenAI Function Calling"""
import json
import sys
import uuid
from typing import Optional, Callable, TYPE_CHECKING

from cbhcli_pkg.core.session import Session, Message
from cbhcli_pkg.core.model import LLMClient
from cbhcli_pkg.core.tool_executor import ToolExecutor
from cbhcli_pkg.tools.registry import ToolResult
from cbhcli_pkg.core.thinking_display import ThinkingDisplay
from cbhcli_pkg.core.response_cleaner import MarkdownTableBuffer
from cbhcli_pkg.core.constants import (
    MAX_TOOL_ROUNDS, MAX_TOOL_OUTPUT_LENGTH, API_TEMPERATURE,
    MAX_REFLECTION_RETRIES, THINKING_MAX_LINES,
    C_AI_HINT, C_AI_TEXT, C_ERROR, C_RESET, C_DIM,
    C_SUBAGENT_HINT, C_SUBAGENT_TEXT, C_SUBAGENT_DIM
)

if TYPE_CHECKING:
    from cbhcli_pkg.context.token_counter import TokenCounter


class AIHandler:
    """处理AI请求和响应

    工具调用方式：纯 OpenAI Function Calling。

    任务管理架构：
    - Todo 工具 — 顶层任务拆分与进度追踪
    - delegate_task 工具 — 独立子任务委托子Agent执行
    - 自我反思 — 工具失败时自动分析并重试
    """

    def __init__(
        self,
        llm_client: LLMClient,
        session: Session,
        tool_executor: ToolExecutor,
        token_counter: 'TokenCounter',
        is_subagent: bool = False
    ):
        self.llm_client = llm_client
        self.session = session
        self.tool_executor = tool_executor
        self.token_counter = token_counter
        self.is_subagent = is_subagent
        self._on_memory_update: Optional[Callable] = None
        self._failure_counts: dict[str, int] = {}
        self.subagent_scheduler = None  # 由 app.py 注入
        self.agent_name: str = "main"   # 由 app.py 注入

        # 根据是否为子agent选择颜色
        if is_subagent:
            self._c_hint = C_SUBAGENT_HINT
            self._c_text = C_SUBAGENT_TEXT
            self._c_dim = C_SUBAGENT_DIM
            self._label = "[SubAgent] "
        else:
            self._c_hint = C_AI_HINT
            self._c_text = C_AI_TEXT
            self._c_dim = C_DIM
            self._label = ""
            
        # 思考内容滚动显示管理器
        self.thinking_display = ThinkingDisplay(max_lines=THINKING_MAX_LINES, label=self._label)

    # ==================================================================
    #  主流程
    # ==================================================================

    def process_request(self, user_input: str, images: list[str] = None) -> str:
        """处理用户请求

        流程:
        1. ReAct 循环: AI 响应 → Function Calling → 执行 → 继续
        2. AI 在循环中通过 Function Calling 调用 Todo 工具管理进度
        3. 工具失败时反思提示附着在 tool 输出中（不破坏消息序列结构）

        Args:
            user_input: 用户输入
            images: 图片列表（base64编码的图片数据）

        Returns:
            最终的AI响应
        """
        # 添加用户消息
        user_msg = Message(
            role="user",
            content=user_input,
            token_count=self.token_counter.count_tokens(user_input),
            images=images if images else None
        )
        self.session.messages.append(user_msg)

        # 重置失败计数
        self._failure_counts.clear()

        # --- ReAct 循环 ---
        for round_idx in range(MAX_TOOL_ROUNDS):
            messages = self.session.get_context_messages()

            ai_response, reasoning_tokens, reasoning_content, tool_calls = self._get_ai_response(
                messages, round_idx
            )

            if tool_calls:
                self._execute_tools(tool_calls, ai_response, reasoning_tokens,
                                    reasoning_content)
            else:
                # 没有工具调用 → 结束
                content_tokens = self.token_counter.count_tokens(ai_response)
                assistant_msg = Message(
                    role="assistant",
                    content=ai_response,
                    token_count=content_tokens + reasoning_tokens,
                    reasoning_content=reasoning_content or None
                )
                self.session.messages.append(assistant_msg)

                if self._on_memory_update:
                    self._on_memory_update(user_input, ai_response)

                return ai_response

        return "达到最大工具调用轮数"

    # ==================================================================
    #  流式响应 + Function Calling 收集
    # ==================================================================

    def _get_ai_response(
        self, messages: list, round_idx: int
    ) -> tuple[str, int, str, list[dict]]:
        """流式获取AI响应，同时收集 Function Calling 工具调用

        Returns:
            (AI文本内容, 思考token数, 思考原文, 工具调用列表)
            工具调用列表格式: [{"id": "...", "name": "...", "arguments": {...}}]
        """
        if round_idx == 0:
            print(f"\n{self._c_hint}{self._label}AI正在分析您的请求...{C_RESET}")

        print(f"\n{self._c_text}{self._label}AI: {C_RESET}", end='', flush=True)

        ai_response = ""
        is_reasoning = False
        reasoning_buffer = ""
        tc_buffer = {}  # index -> {id, name, arguments_str}
        table_buffer = MarkdownTableBuffer()  # Markdown 表格对齐缓冲

        try:
            openai_tools = self.tool_executor.tool_registry.get_openai_tools()
            stream_kwargs = {"temperature": API_TEMPERATURE}
            if openai_tools:
                stream_kwargs["tools"] = openai_tools
                stream_kwargs["tool_choice"] = "auto"

            for chunk_type, content in self.llm_client.chat_stream(messages, **stream_kwargs):
                if chunk_type == "reasoning":
                    if not is_reasoning:
                        is_reasoning = True
                        # 启动思考内容滚动显示
                        self.thinking_display.start_thinking()
                    reasoning_buffer += content
                    # 将思考内容添加到滚动显示区域
                    self.thinking_display.add_content(content)

                elif chunk_type == "tool_calls":
                    # 收集 Function Calling 增量数据
                    try:
                        tool_calls_data = json.loads(content)
                        for tc in tool_calls_data:
                            idx = tc.get('index', 0)
                            if idx not in tc_buffer:
                                tc_buffer[idx] = {'id': '', 'name': '', 'arguments': ''}
                            if tc.get('id'):
                                tc_buffer[idx]['id'] = tc['id']
                            func = tc.get('function', {})
                            if func.get('name'):
                                tc_buffer[idx]['name'] = func['name']
                            if 'arguments' in func:
                                tc_buffer[idx]['arguments'] += func['arguments']
                    except json.JSONDecodeError:
                        pass

                elif chunk_type == "content":
                    if is_reasoning:
                        is_reasoning = False
                        # 完成思考内容滚动显示
                        self.thinking_display.finish_thinking()

                    # 通过表格缓冲器处理内容，实现表格自动对齐
                    aligned_content = table_buffer.feed(content)
                    ai_response += aligned_content

            # 流式结束 — 输出表格缓冲器中的剩余内容
            remaining = table_buffer.flush()
            if remaining:
                ai_response += remaining

            # 构造结构化 tool_calls
            tool_calls = []
            if tc_buffer:
                for idx in sorted(tc_buffer.keys()):
                    tc = tc_buffer[idx]
                    if tc['name']:
                        tc_id = tc['id'] or f"call_{uuid.uuid4().hex[:8]}"
                        try:
                            args = json.loads(tc['arguments']) if tc['arguments'] else {}
                        except json.JSONDecodeError:
                            args = {}
                        tool_calls.append({
                            "id": tc_id,
                            "name": tc['name'],
                            "arguments": args,
                        })
                        print(f"\n{self._c_dim}🔧 {tc['name']}({tc['arguments'][:100]}){C_RESET}")

            # 确保在流式结束时关闭思考显示（如果仍在思考中）
            if is_reasoning and self.thinking_display.is_thinking:
                self.thinking_display.finish_thinking()

            print()

        except KeyboardInterrupt:
            # Ctrl+C 中断：强制清理思考显示，恢复终端状态
            self.thinking_display.cleanup()
            raise

        except Exception as e:
            # 确保在异常情况下也能关闭思考显示
            if is_reasoning and self.thinking_display.is_thinking:
                self.thinking_display.finish_thinking()
            print(f"\n{C_ERROR}AI调用失败: {str(e)}{C_RESET}")
            raise

        reasoning_tokens = self.token_counter.count_tokens(reasoning_buffer) if reasoning_buffer else 0
        return ai_response, reasoning_tokens, reasoning_buffer, tool_calls

    # ==================================================================
    #  工具执行（基于 Function Calling 结构化数据）
    # ==================================================================

    def _execute_tools(
        self, tool_calls: list[dict], ai_response: str, reasoning_tokens: int = 0,
        reasoning_content: str = ""
    ):
        """执行 Function Calling 返回的工具调用

        Args:
            tool_calls: [{"id", "name", "arguments"}] 由 _get_ai_response 构建
            ai_response: 伴随的文本内容
            reasoning_tokens: 思考内容 token 数
            reasoning_content: 思考原文（DeepSeek 等模型需要传回 API）
        """
        # 去重 + 解析工具名
        valid_calls = []
        seen = set()
        for tc in tool_calls:
            tool_name = tc["name"]
            resolved = self._resolve_tool_name(tool_name)
            if not resolved:
                continue
            args_json = json.dumps(tc.get("arguments", {}), sort_keys=True)
            key = (resolved, args_json)
            if key in seen:
                continue
            seen.add(key)
            valid_calls.append({
                "id": tc["id"],
                "tool": resolved,
                "arguments": tc.get("arguments", {}),
            })

        if not valid_calls:
            return

        # 构建 OpenAI 格式的 tool_calls 记录到会话
        openai_tool_calls = []
        for tc in valid_calls:
            openai_tool_calls.append({
                "id": tc["id"],
                "type": "function",
                "function": {
                    "name": tc["tool"],
                    "arguments": json.dumps(tc["arguments"])
                }
            })

        # 计算 token
        content_tokens = self.token_counter.count_tokens(ai_response) if ai_response else 10
        tool_calls_tokens = self.token_counter.count_tokens(
            json.dumps(openai_tool_calls, ensure_ascii=False)
        ) if openai_tool_calls else 0
        total_tokens = content_tokens + reasoning_tokens + tool_calls_tokens

        # 记录 assistant 消息（含 tool_calls）
        assistant_msg = Message(
            role="assistant",
            content=ai_response if ai_response else "",
            token_count=total_tokens,
            tool_calls=openai_tool_calls,
            reasoning_content=reasoning_content or None
        )
        self.session.messages.append(assistant_msg)

        # 逐个执行（每个 tool_call 必须产生对应的 tool 消息，否则 API 会报错）
        for tc in valid_calls:
            try:
                result = self.tool_executor.execute_with_display(
                    tc["tool"],
                    tc["arguments"],
                    tc["id"]
                )

                if result.success:
                    output = result.output[:MAX_TOOL_OUTPUT_LENGTH]
                else:
                    # 失败时：优先使用 output（包含完整 traceback），其次用 error
                    if result.output:
                        output = result.output[:MAX_TOOL_OUTPUT_LENGTH]
                    else:
                        output = f"错误: {result.error}"
            except Exception as e:
                # 兜底：确保即使 execute_with_display 异常也能产生 tool 消息
                output = f"工具执行异常: {str(e)}"
                result = ToolResult(success=False, output=output, error=str(e))

            # 工具失败时：将反思提示附着在 tool 输出中（而非注入 system 消息）
            # 保持标准 OAI 消息序列不变，有利于 LLM 前缀缓存命中
            if not result.success:
                fail_key = tc["tool"]
                self._failure_counts[fail_key] = self._failure_counts.get(fail_key, 0) + 1
                if self._failure_counts[fail_key] <= MAX_REFLECTION_RETRIES:
                    retry_count = self._failure_counts[fail_key]
                    remaining = MAX_REFLECTION_RETRIES - retry_count
                    print(f"\n{self._c_hint}🔁 {tc['tool']} 执行失败，正在自我反思 (重试 {retry_count}/{MAX_REFLECTION_RETRIES})...{C_RESET}")
                    reflection_hint = (
                        f"[反思提示] 上一个工具调用失败，请分析原因并重试。\n"
                        f"失败工具: {tc['tool']}\n"
                        f"参数: {json.dumps(tc['arguments'], ensure_ascii=False)}\n"
                        f"剩余重试: {remaining}/{MAX_REFLECTION_RETRIES}\n\n"
                        f"--- 原始输出 ---\n{output}"
                    )
                    output = reflection_hint
                else:
                    print(f"\n{self._c_hint}❌ {tc['tool']} 已达最大重试次数 ({MAX_REFLECTION_RETRIES})，放弃重试{C_RESET}")
            else:
                self._failure_counts.pop(tc["tool"], None)

            tool_msg = Message(
                role="tool",
                content=output,
                token_count=self.token_counter.count_tokens(output),
                tool_call_id=tc["id"]
            )
            tool_msg.metadata = {"tool_name": tc["tool"], "success": getattr(result, 'success', False)}
            self.session.messages.append(tool_msg)

    def _resolve_tool_name(self, name: str) -> Optional[str]:
        """模糊匹配工具名，返回注册名或 None"""
        tool = self.tool_executor.tool_registry.fuzzy_get(name)
        return tool.name if tool else None

    def on_memory_update(self, callback: Callable):
        self._on_memory_update = callback
