"""AI请求处理器 - 纯 OpenAI Function Calling"""
import json
import re
import sys
import uuid
from typing import Optional, Callable, TYPE_CHECKING

from cbhcli_pkg.core.session import Session, Message
from cbhcli_pkg.core.model import LLMClient
from cbhcli_pkg.core.tool_executor import ToolExecutor
from cbhcli_pkg.tools.registry import ToolResult
from cbhcli_pkg.core.thinking_display import ThinkingDisplay
from cbhcli_pkg.core.markdown_renderer import MarkdownStreamRenderer
from cbhcli_pkg.core.loop_detector import ToolCallTracker, TextLoopDetector
from cbhcli_pkg.core.constants import (
    MAX_TOOL_ROUNDS, MAX_TOOL_OUTPUT_LENGTH, API_TEMPERATURE,
    MAX_REFLECTION_RETRIES, THINKING_MAX_LINES,
    C_AI_HINT, C_AI_TEXT, C_ERROR, C_RESET, C_DIM,
    C_SUBAGENT_HINT, C_SUBAGENT_TEXT, C_SUBAGENT_DIM
)

if TYPE_CHECKING:
    from cbhcli_pkg.context.token_counter import TokenCounter
    from cbhcli_pkg.context.compressor import ContextCompressor
    from cbhcli_pkg.core.session import ContextWindow


def _fix_unicode_escapes(obj):
    """修复 LLM 返回的双斜杠 Unicode 转义序列

    LLM 有时会在 JSON 字符串中返回 \u2192（双斜杠）而不是 \u2192（单斜杠），
    导致 json.loads 解析后保留字面字符串 '\u2192'（6个字符）而非实际箭头 '→'。
    此函数递归地将这些字面 Unicode 转义序列转换为实际字符。

    Args:
        obj: 从 json.loads 解析出的对象（dict/list/str/其他）

    Returns:
        修复后的对象
    """
    if isinstance(obj, dict):
        return {k: _fix_unicode_escapes(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [_fix_unicode_escapes(item) for item in obj]
    elif isinstance(obj, str):
        # 检测并替换字面 \uXXXX 序列（双斜杠转义的结果）
        # 匹配字面字符串 \uXXXX（其中 X 是十六进制数字）
        pattern = r'\\u([0-9a-fA-F]{4})'

        def replace_match(match):
            hex_code = match.group(1)
            try:
                # 将十六进制转换为实际字符
                return chr(int(hex_code, 16))
            except (ValueError, OverflowError):
                # 转换失败则保留原样
                return match.group(0)

        # 先尝试替换，如果字符串中没有 \uXXXX 模式则返回原字符串
        result = re.sub(pattern, replace_match, obj)
        return result
    else:
        return obj


def repair_tool_messages(messages: list) -> list:
    """修复 tool_calls 消息序列不完整问题（DeepSeek 等 API 严格要求）。

    触发 400 错误 "An assistant message with 'tool_calls' must be followed by
    tool messages responding to each 'tool_call_id'" 的典型场景：
    1. assistant(tool_calls) 后缺少对应 tool 消息
       （工具执行被 Ctrl+C / abort / 异常中断，仅记录了 assistant 消息）
    2. user 消息（如 image 工具直发的带图消息）插在 tool 消息之间
       （多个 tool_call 且其中某个返回图片时，图片 user 消息插队）

    修复策略：
    - 扫描每个带 tool_calls 的 assistant，收集其后连续的工具区
      （tool 消息 + 其间插入的 user 消息）
    - 缺失的 tool_call_id 补占位 tool 消息（内容提示模型结果缺失）
    - 插队的 user 消息（位于最后一个 tool 消息之前）移到工具组之后

    Args:
        messages: Session.get_context_messages() 输出的 dict 列表

    Returns:
        修复后的新列表（不修改原列表 / 会话内部状态）
    """
    result = []
    i, n = 0, len(messages)
    while i < n:
        msg = messages[i]
        if msg.get("role") != "assistant" or not msg.get("tool_calls"):
            result.append(msg)
            i += 1
            continue

        tc_ids = {tc.get("id") for tc in msg["tool_calls"] if tc.get("id")}

        # 扫描工具区：连续的 tool 消息 + 其间插入的 user 消息
        j = i + 1
        region = []
        while j < n:
            m = messages[j]
            r = m.get("role")
            if r == "tool" and m.get("tool_call_id") in tc_ids:
                region.append(m)
                j += 1
            elif r == "user":
                region.append(m)  # 可能是插队消息，稍后判定
                j += 1
            else:
                break

        result.append(msg)

        # 工具区内的 tool 消息（保持原顺序）
        group_tools = [m for m in region if m.get("role") == "tool"]
        users = [m for m in region if m.get("role") == "user"]

        result.extend(group_tools)

        # 补全缺失的 tool 消息（工具被中断/异常时只记录了 assistant 消息）
        seen = {m.get("tool_call_id") for m in group_tools}
        for tid in tc_ids:
            if tid not in seen:
                result.append({
                    "role": "tool",
                    "tool_call_id": tid,
                    "content": "[系统补全] 该工具调用的结果缺失（可能因中断或异常未执行），"
                               "请根据已有上下文继续任务，不要重复调用。",
                })

        # 插队 user（位于最后一个 tool 消息之前）移到工具组之后；
        # 工具组之后的 user 是正常新轮次，保持原顺序
        if group_tools:
            last_tool_idx = max(
                idx for idx, m in enumerate(region) if m.get("role") == "tool")
            interleaved = [
                m for idx, m in enumerate(region)
                if m.get("role") == "user" and idx < last_tool_idx
            ]
            trailing = [
                m for idx, m in enumerate(region)
                if m.get("role") == "user" and idx >= last_tool_idx
            ]
            result.extend(interleaved)
            result.extend(trailing)
        else:
            # 没有任何 tool 消息（全部缺失已补占位）：user 均为正常轮次
            result.extend(users)

        i = j
    return result


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
        is_subagent: bool = False,
        display_label: str = ""
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
        self.fallback_models: list[str] = []  # 备用主模型名称列表，由 app.py 注入
        self.context_compressor: Optional['ContextCompressor'] = None  # 由 app.py 注入
        self.context_window: Optional['ContextWindow'] = None  # 由 app.py 注入
        self.auto_compress: bool = True  # 由 app.py 注入
        # 链条下游 Agent 事件回调（Web 端 SSE 推送用）
        # 签名: (event_type, agent_name, **data) -> None
        self._chain_event_callback: Optional[Callable] = None

        # 根据是否为子agent选择颜色
        if is_subagent:
            self._c_hint = C_SUBAGENT_HINT
            self._c_text = C_SUBAGENT_TEXT
            self._c_dim = C_SUBAGENT_DIM
            self._label = display_label or "[SubAgent] "
        else:
            self._c_hint = C_AI_HINT
            self._c_text = C_AI_TEXT
            self._c_dim = C_DIM
            self._label = display_label
            
        # 思考内容滚动显示管理器
        self.thinking_display = ThinkingDisplay(max_lines=THINKING_MAX_LINES, label=self._label)

    def _emit_chain_event(self, event_type: str, **data):
        """发送链条事件到回调（Web 端 SSE 推送用）"""
        cb = self._chain_event_callback
        if cb:
            try:
                cb(event_type, self.agent_name, **data)
            except Exception:
                pass

    # ==================================================================
    #  主流程
    # ==================================================================

    def process_request(self, user_input: str) -> str:
        """处理用户请求

        流程:
        1. ReAct 循环: AI 响应 → Function Calling → 执行 → 继续
        2. AI 在循环中通过 Function Calling 调用 Todo 工具管理进度
        3. 工具失败时反思提示附着在 tool 输出中（不破坏消息序列结构）

        Args:
            user_input: 用户输入

        Returns:
            最终的AI响应
        """
        # 添加用户消息
        user_msg = Message(
            role="user",
            content=user_input,
            token_count=self.token_counter.count_tokens(user_input),
        )
        self.session.messages.append(user_msg)

        # 重置失败计数
        self._failure_counts.clear()

        # 死循环检测器（每个用户请求独立）
        self._loop_tracker = ToolCallTracker()
        self._loop_aborted = False

        # --- ReAct 循环 ---
        for round_idx in range(MAX_TOOL_ROUNDS):
            # 检查上下文是否接近上限，自动压缩
            self._check_and_compress_in_react(round_idx)

            messages = self.session.get_context_messages()
            # 防御性修复：补全缺失 tool 消息 / 移动插队的 user 消息
            # （DeepSeek 等 API 严格要求 assistant(tool_calls) 后紧跟全部 tool 消息）
            messages = repair_tool_messages(messages)

            ai_response, reasoning_tokens, reasoning_content, tool_calls = self._get_ai_response(
                messages, round_idx
            )

            if tool_calls:
                self._execute_tools(tool_calls, ai_response, reasoning_tokens,
                                    reasoning_content)
                # 死循环熔断：干预次数达上限，终止本轮任务
                if self._loop_aborted:
                    print(f"\n{C_ERROR}🛑 检测到模型多次陷入死循环，已熔断本轮任务。"
                          f"建议：换一种任务描述 / /new 重开会话 / 切换模型{C_RESET}")
                    return "（任务因死循环熔断而终止）"
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

        当主模型异常时，自动切换到备用模型重试。

        Returns:
            (AI文本内容, 思考token数, 思考原文, 工具调用列表)
            工具调用列表格式: [{"id": "...", "name": "...", "arguments": {...}}]
        """
        # 尝试主模型 + 备用模型
        try:
            return self._stream_with_model(self.llm_client, messages, round_idx)
        except KeyboardInterrupt:
            raise
        except Exception as primary_error:
            # 主模型失败，尝试备用模型
            if not self.fallback_models:
                raise

            print(f"\n{C_ERROR}⚠️  主模型调用失败: {primary_error}{C_RESET}")
            return self._try_fallback_models(messages, round_idx, primary_error)

    def _try_fallback_models(
        self, messages: list, round_idx: int, primary_error: Exception
    ) -> tuple[str, int, str, list[dict]]:
        """尝试备用模型列表，依次重试直到成功或全部失败"""
        from cbhcli_pkg.config.global_config import GlobalConfig
        config = GlobalConfig()

        for i, model_name in enumerate(self.fallback_models):
            model_config = config.get_model(model_name)
            if not model_config:
                print(f"\n{C_DIM}🔄 备用模型 '{model_name}' 未找到配置，跳过{C_RESET}")
                continue

            print(f"\n{C_DIM}🔄 切换到备用模型 '{model_name}' (第{i+1}/{len(self.fallback_models)}个)...{C_RESET}")
            try:
                fallback_client = LLMClient(model_config)
                return self._stream_with_model(fallback_client, messages, round_idx)
            except KeyboardInterrupt:
                raise
            except Exception as fallback_error:
                print(f"\n{C_ERROR}⚠️  备用模型 '{model_name}' 也失败: {fallback_error}{C_RESET}")
                continue

        # 所有备用模型都失败
        raise Exception(
            f"主模型和所有备用模型均失败。主模型错误: {primary_error}"
        )

    def _stream_with_model(
        self, client: LLMClient, messages: list, round_idx: int
    ) -> tuple[str, int, str, list[dict]]:
        """使用指定模型客户端进行流式请求

        Args:
            client: LLM 客户端实例
            messages: 消息列表
            round_idx: 当前 ReAct 轮次

        Returns:
            (AI文本内容, 思考token数, 思考原文, 工具调用列表)
        """
        if round_idx == 0:
            print(f"\n{self._c_hint}{self._label}AI正在分析您的请求...{C_RESET}")

        ai_response = ""
        is_reasoning = False
        reasoning_buffer = ""
        tc_buffer = {}  # index -> {id, name, arguments_str}
        md_renderer = MarkdownStreamRenderer()  # Markdown 流式渲染器
        # 设置前缀（含 ANSI 颜色码），feed() 首次调用时输出
        md_renderer.set_prefix(f"\n{self._c_text}{self._label}AI: {C_RESET}")
        # 文本复读检测（思考流 / 正文流各一个）
        reasoning_loop = TextLoopDetector()
        content_loop = TextLoopDetector()
        text_loop_hit = False

        try:
            openai_tools = self.tool_executor.tool_registry.get_openai_tools()
            stream_kwargs = {"temperature": API_TEMPERATURE}
            if openai_tools:
                stream_kwargs["tools"] = openai_tools
                stream_kwargs["tool_choice"] = "auto"

            stream = client.chat_stream(messages, **stream_kwargs)
            for chunk_type, content in stream:
                if chunk_type == "reasoning":
                    if not is_reasoning:
                        is_reasoning = True
                        # 启动思考内容滚动显示
                        self.thinking_display.start_thinking()
                    reasoning_buffer += content
                    # 将思考内容添加到滚动显示区域
                    self.thinking_display.add_content(content)
                    # 链条事件回调（Web SSE 推送）
                    self._emit_chain_event("reasoning", content=content)
                    # 思考内容复读检测
                    if reasoning_loop.feed(content):
                        text_loop_hit = True
                        print(f"\n{self._c_dim}⚠️ 检测到思考过程陷入重复，已截断{C_RESET}")
                        stream.close()
                        break

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

                    # 通过 Markdown 流式渲染器处理内容
                    raw_content = md_renderer.feed(content)
                    ai_response += raw_content
                    # 链条事件回调（Web SSE 推送）
                    self._emit_chain_event("content", content=content)
                    # 正文复读检测
                    if content_loop.feed(content):
                        text_loop_hit = True
                        ai_response = content_loop.truncated_text()
                        print(f"\n{self._c_dim}⚠️ 检测到回复内容陷入重复，已截断{C_RESET}")
                        stream.close()
                        break

            # 流式结束 — 完成 Markdown 渲染（清除纯文本+前缀，重新渲染）
            md_renderer.flush()

            # 构造结构化 tool_calls
            tool_calls = []
            if tc_buffer:
                for idx in sorted(tc_buffer.keys()):
                    tc = tc_buffer[idx]
                    if tc['name']:
                        tc_id = tc['id'] or f"call_{uuid.uuid4().hex[:8]}"
                        try:
                            args = json.loads(tc['arguments']) if tc['arguments'] else {}
                            # 修复 LLM 可能返回的双斜杠 Unicode 转义序列（\\u2192 → →）
                            args = _fix_unicode_escapes(args)
                        except json.JSONDecodeError:
                            args = {}
                        tool_calls.append({
                            "id": tc_id,
                            "name": tc['name'],
                            "arguments": args,
                        })
                        print(f"\n{self._c_dim}🔧 {tc['name']}({tc['arguments'][:100]}){C_RESET}")

            # 文本复读截断：若本轮还有工具调用，提示附在回复里让模型下轮看到
            if text_loop_hit and tool_calls:
                note = ("\n\n[系统提示] 上一条输出陷入重复循环，已被系统截断。"
                        "请避免重复内容，简明扼要地继续任务。")
                ai_response = (ai_response or "") + note
                if self.tool_executor.tracer:
                    self.tool_executor.tracer.log_loop(
                        "text_loop", detail="流式输出复读截断")

            # 确保在流式结束时关闭思考显示（如果仍在思考中）
            if is_reasoning and self.thinking_display.is_thinking:
                self.thinking_display.finish_thinking()

            print()

        except KeyboardInterrupt:
            # Ctrl+C 中断：强制清理渲染器和思考显示，恢复终端状态
            md_renderer.cleanup()
            self.thinking_display.cleanup()
            raise

        except Exception as e:
            # 确保在异常情况下也能关闭渲染器和思考显示
            if md_renderer.started:
                md_renderer.cleanup()
            if is_reasoning and self.thinking_display.is_thinking:
                self.thinking_display.finish_thinking()
            raise

        reasoning_tokens = self.token_counter.count_tokens(reasoning_buffer) if reasoning_buffer else 0
        return ai_response, reasoning_tokens, reasoning_buffer, tool_calls

    # ==================================================================
    #  ReAct 循环内上下文压缩
    # ==================================================================

    def _check_and_compress_in_react(self, round_idx: int):
        """在 ReAct 循环中检查上下文使用量，超过阈值时自动压缩。

        与 app.py 中的 _check_and_compress_context 不同，这里是在工具调用
        循环过程中检查，防止多轮工具调用导致上下文溢出。

        压缩策略与 app.py 一致：保留最早2轮 + 最近3轮，中间部分生成摘要。
        压缩后 session.messages 被替换，下一轮 LLM 调用将使用压缩后的上下文。
        """
        if not self.context_compressor or not self.context_window:
            return

        if not self.auto_compress:
            return

        # 使用 token_counter 精确计算（含消息结构开销）
        total_tokens = self.session.get_total_tokens(self.token_counter)
        self.context_window.update(total_tokens)

        if not self.context_window.needs_compression():
            return

        # 上下文接近上限，执行压缩
        print(f"\n{self._c_dim}📦 上下文接近上限 ({self.context_window.get_status_text()})")
        print(f"{self._c_dim}   正在自动压缩上下文...{C_RESET}")

        target_tokens = self.context_window.trigger_threshold()
        success = self.context_compressor.compress(self.session, target_tokens)

        if success:
            # 压缩后重新计算 token
            new_tokens = self.session.get_total_tokens(self.token_counter)
            self.context_window.update(new_tokens)
            print(f"{self._c_dim}   ✅ 上下文已压缩 ({self.context_window.get_status_text()}){C_RESET}")
        else:
            print(f"{self._c_dim}   ⚠️  压缩失败（消息太少或生成摘要异常），继续执行{C_RESET}")

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
            # 所有工具调用都无法解析（工具名错误/重复），必须向会话添加提示
            # 否则下一轮循环用相同上下文，AI 可能返回相同无效 tool_calls → 无限循环
            invalid_names = [tc["name"] for tc in tool_calls]
            error_msg = (
                f"⚠️ 工具调用失败：以下工具名无法识别或重复调用：{', '.join(invalid_names)}\n"
                f"可用工具：{', '.join(self.tool_executor.tool_registry.get_available_tools())}\n"
                f"请使用正确的工具名重新调用。"
            )
            print(f"\n{C_ERROR}{error_msg}{C_RESET}")
            # 添加 assistant 消息（含 tool_calls）和 tool 错误消息，保持 OAI 消息序列完整
            assistant_msg = Message(
                role="assistant",
                content=ai_response if ai_response else "",
                token_count=self.token_counter.count_tokens(ai_response or ""),
                tool_calls=[{
                    "id": tc["id"],
                    "type": "function",
                    "function": {"name": tc["name"], "arguments": json.dumps(tc.get("arguments", {}))}
                } for tc in tool_calls],
                reasoning_content=reasoning_content or None
            )
            self.session.messages.append(assistant_msg)
            for tc in tool_calls:
                tool_msg = Message(
                    role="tool",
                    content=error_msg,
                    token_count=self.token_counter.count_tokens(error_msg),
                    tool_call_id=tc["id"]
                )
                tool_msg.metadata = {"tool_name": tc["name"], "success": False}
                self.session.messages.append(tool_msg)
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
        # 图片 user 消息延迟到所有 tool 消息之后统一追加，避免插在 tool 消息之间
        # 导致 DeepSeek 等 API 报 "insufficient tool messages following tool_calls"
        pending_image_msgs: list = []
        try:
            for tc in valid_calls:
                # ---- 死循环检测（同参数重复 / 周期震荡）----
                verdict, loop_msg = "ok", None
                if getattr(self, "_loop_tracker", None) is not None:
                    verdict, loop_msg = self._loop_tracker.check(
                        tc["tool"], tc["arguments"])

                if verdict == "abort":
                    self._loop_aborted = True
                    if self.tool_executor.tracer:
                        self.tool_executor.tracer.log_loop("abort", tc["tool"])
                    result = ToolResult(
                        success=False,
                        output="🛑 [系统熔断] 多次陷入死循环，本轮任务已终止。",
                        error="loop abort")
                    output = result.output
                elif verdict == "block":
                    print(f"\n{self._c_dim}🛑 死循环熔断: 已阻止重复调用 "
                          f"{tc['tool']}（同参数第4+次），已告知模型换策略{C_RESET}")
                    if self.tool_executor.tracer:
                        self.tool_executor.tracer.log_loop("block", tc["tool"])
                    result = ToolResult(success=False, output=loop_msg,
                                        error="loop blocked")
                    output = loop_msg
                else:
                    if verdict == "warn":
                        print(f"\n{self._c_dim}⚠️ 检测到疑似死循环（{tc['tool']} "
                              f"重复调用），已在结果中提醒模型{C_RESET}")
                        if self.tool_executor.tracer:
                            self.tool_executor.tracer.log_loop("warn", tc["tool"])
                    try:
                        # 链条事件回调：工具调用开始
                        self._emit_chain_event("tool_call", tool_name=tc["tool"],
                                               arguments=tc["arguments"])
                        result = self.tool_executor.execute_with_display(
                            tc["tool"],
                            tc["arguments"],
                            tc["id"]
                        )
                        # 链条事件回调：工具执行结果
                        self._emit_chain_event(
                            "tool_result", tool_name=tc["tool"],
                            success=result.success,
                            output=(result.output or "")[:500],
                            error=result.error or "")

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

                    # 软警告附加到工具结果尾部（模型最重视的信息通道）
                    if verdict == "warn" and loop_msg:
                        output = f"{output}\n\n{loop_msg}"

                # 工具失败时：将反思提示附着在 tool 输出中（而非注入 system 消息）
                # 保持标准 OAI 消息序列不变，有利于 LLM 前缀缓存命中
                # （死循环熔断的失败不进入反思重试，避免雪上加霜）
                if not result.success and verdict == "ok":
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

                # 工具结果携带图片（image 工具直发模式）：延迟到所有 tool 消息之后
                # 统一追加带图用户消息，使支持视觉的主模型直接在当前会话中查看图片
                if result.success and getattr(result, "images", None):
                    vision_prompt = (result.metadata or {}).get("vision_prompt", "")
                    note = f"[image 工具传入 {len(result.images)} 张图片]"
                    if vision_prompt:
                        note += f" 识别需求: {vision_prompt}"
                    img_msg = Message(
                        role="user",
                        content=note,
                        token_count=self.token_counter.count_tokens(note),
                        images=result.images
                    )
                    pending_image_msgs.append(img_msg)
        except KeyboardInterrupt:
            # 用户在工具执行时 Ctrl+C 中断：补全未执行的 tool 消息，
            # 保持 OAI 消息序列完整（assistant(tool_calls) 后必须有全部 tool 消息）
            executed_ids = {
                m.tool_call_id for m in self.session.messages if m.role == "tool"
            }
            for tc in valid_calls:
                if tc["id"] in executed_ids:
                    continue
                tool_msg = Message(
                    role="tool",
                    content="[系统补全] 工具执行被用户中断，未产生结果。请根据上下文继续任务。",
                    token_count=self.token_counter.count_tokens(
                        "[系统补全] 工具执行被用户中断，未产生结果。请根据上下文继续任务。"),
                    tool_call_id=tc["id"]
                )
                tool_msg.metadata = {"tool_name": tc["tool"], "success": False}
                self.session.messages.append(tool_msg)
            raise

        # 所有 tool 消息添加完成后，统一追加图片 user 消息（避免插队破坏序列）
        for img_msg in pending_image_msgs:
            self.session.messages.append(img_msg)

    def _resolve_tool_name(self, name: str) -> Optional[str]:
        """模糊匹配工具名，返回注册名或 None"""
        tool = self.tool_executor.tool_registry.fuzzy_get(name)
        return tool.name if tool else None

    def on_memory_update(self, callback: Callable):
        self._on_memory_update = callback
