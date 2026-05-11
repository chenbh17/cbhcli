"""AI请求处理器 - 纯 OpenAI Function Calling"""
import re
import json
import sys
import uuid
from typing import Optional, Callable, TYPE_CHECKING

from cbhcli_pkg.core.session import Session, Message
from cbhcli_pkg.core.model import LLMClient
from cbhcli_pkg.core.tool_executor import ToolExecutor
from cbhcli_pkg.core.constants import (
    MAX_TOOL_ROUNDS, MAX_TOOL_OUTPUT_LENGTH, API_TEMPERATURE,
    MAX_REFLECTION_RETRIES, PLANNING_MIN_LENGTH,
    C_AI_HINT, C_AI_TEXT, C_ERROR, C_RESET, C_DIM,
    C_SUBAGENT_HINT, C_SUBAGENT_TEXT, C_SUBAGENT_DIM
)

if TYPE_CHECKING:
    from cbhcli_pkg.context.token_counter import TokenCounter

# ===================================================================
#  提示词
# ===================================================================

# 规划阶段提示词 - 引导 AI 使用 Todo 工具
PLANNING_PROMPT = (
    "用户的请求较为复杂，涉及多个步骤。请按以下方式处理：\n\n"
    "1. 先调用 Todo 工具创建一个完整的计划列表，将任务拆分为多个独立步骤，"
    "每个步骤初始状态设为 pending。\n\n"
    "2. 然后按顺序执行每个步骤：\n"
    "   - 开始前：调用 Todo 将该步骤标记为 in_progress\n"
    "   - 执行：使用所需工具完成这个步骤\n"
    "   - 完成后：调用 Todo 将该步骤标记为 completed\n\n"
    "3. 如果某个步骤内部仍然复杂（需要多个子操作），可以输出 [PLAN]...[/PLAN] 进一步拆分。\n\n"
    "4. 所有步骤完成后，给出最终总结。\n\n"
    "重要：每次调用 Todo 工具都要传入完整的列表（所有条目及其最新状态）。"
)

# 反思提示词
REFLECTION_PROMPT = (
    "上一个工具调用失败了。请分析失败原因并决定下一步：\n"
    "1. 如果是参数错误，修正参数后重试\n"
    "2. 如果是工具不适用，换一个工具或方法\n"
    "3. 如果无法恢复，向用户说明原因\n\n"
    "失败工具: {tool_name}\n"
    "参数: {arguments}\n"
    "错误信息: {error}"
)

# 规划关键词（包含这些词时更可能需要规划）
_PLANNING_KEYWORDS = [
    # 中文连接词
    '并且', '然后', '接着', '同时', '以及', '之后',
    # 中文序数词
    '第一', '第二', '第三', '首先', '最后', '其次',
    # 中文步骤/任务相关
    '规划', '计划', '分步', '多步', '任务', '步骤',
    '阶段', '流程', '方案', '实现', '完成',
    # 中文数字编号
    '1、', '2、', '3、', '1.', '2.', '3.',
    '1)', '2)', '3)',
    # 中文批量/多个
    '批量', '多个', '所有', '逐个', '依次', '分别',
    '每个', '遍历', '循环',
    # 中文复杂任务词
    '重构', '迁移', '部署', '安装', '配置', '搭建',
    '整理', '优化', '修改', '调整', '升级', '更新',
    # 英文关键词
    'and then', 'after that', 'first', 'finally', 'step',
    'plan', 'task', 'phase', 'stage', 'workflow',
    'batch', 'multiple', 'each', 'iterate',
    'refactor', 'migrate', 'deploy', 'setup', 'configure',
]


class AIHandler:
    """处理AI请求和响应

    工具调用方式：纯 OpenAI Function Calling
    不再从 content 中解析裸 JSON / XML / 代码块格式的工具调用。

    三层任务管理架构：
    - Layer 1: Todo 工具 — 顶层任务拆分与进度追踪
    - Layer 2: [PLAN] 标签 — 单个 Todo 步骤内部的细化拆分
    - Layer 3: SubAgent — Plan 子步骤的独立执行
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

    # ==================================================================
    #  主流程
    # ==================================================================

    def process_request(self, user_input: str) -> str:
        """处理用户请求

        流程:
        1. 检测是否需要规划 → 注入 Todo 提示
        2. ReAct 循环: AI 响应 → Function Calling → 执行 → 继续
        3. AI 在循环中通过 Function Calling 调用 Todo 工具管理进度
        4. 如果 AI 输出 [PLAN]，则该步骤的子计划交给 SubAgent 执行

        Args:
            user_input: 用户输入

        Returns:
            最终的AI响应
        """
        # 添加用户消息
        user_msg = Message(
            role="user",
            content=user_input,
            token_count=self.token_counter.count_tokens(user_input)
        )
        self.session.messages.append(user_msg)

        # --- Planning 检测 ---
        if self._should_plan(user_input):
            self._inject_planning_hint()

        # 重置失败计数
        self._failure_counts.clear()

        # --- ReAct 循环 ---
        for round_idx in range(MAX_TOOL_ROUNDS):
            messages = self.session.get_context_messages()

            # 工具执行后的轻量提示（不再指定调用格式）
            if round_idx > 0:
                has_tool_results = any(msg.get("role") == "tool" for msg in messages)
                if has_tool_results:
                    hint = {
                        "role": "system",
                        "content": (
                            "工具已执行完毕。请根据工具输出结果继续操作。"
                            "如果已完成所有操作，请直接给出最终答案。"
                            "如果有 Todo 列表，记得更新已完成的步骤并继续下一步。"
                        )
                    }
                    messages = messages + [hint]

            ai_response, reasoning_tokens, reasoning_content, tool_calls = self._get_ai_response(
                messages, round_idx
            )

            # 检查是否有 [PLAN]...[/PLAN]：交给 SubAgent 执行
            if self.subagent_scheduler:
                plan_steps = self._parse_plan(ai_response)
                if len(plan_steps) >= 2:
                    self._execute_plan_with_subagents(
                        plan_steps, ai_response, user_input, reasoning_tokens,
                        reasoning_content
                    )
                    # SubAgent 执行完后，继续 ReAct 循环
                    continue

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
    #  Layer 1: Todo 检测与规划注入
    # ==================================================================

    def _should_plan(self, user_input: str) -> bool:
        """判断是否需要规划"""
        if len(user_input) < PLANNING_MIN_LENGTH:
            return False
        input_lower = user_input.lower()
        keyword_count = sum(1 for kw in _PLANNING_KEYWORDS if kw in input_lower)
        return keyword_count >= 1

    def _inject_planning_hint(self):
        """注入规划提示，引导AI使用Todo工具"""
        hint_msg = Message(
            role="system",
            content=PLANNING_PROMPT,
            token_count=self.token_counter.count_tokens(PLANNING_PROMPT)
        )
        self.session.messages.append(hint_msg)
        print(f"\n{self._c_hint}检测到复杂任务，启用规划模式...{C_RESET}")

    # ==================================================================
    #  Layer 2: [PLAN] 解析 + SubAgent 执行
    # ==================================================================

    def _parse_plan(self, response: str) -> list[str]:
        """从AI响应中解析 [PLAN]...[/PLAN] 的步骤列表"""
        match = re.search(r'\[PLAN\](.*?)\[/PLAN\]', response, re.DOTALL)
        if not match:
            return []

        plan_text = match.group(1)
        steps = []
        for line in plan_text.strip().split('\n'):
            line = line.strip()
            step_match = re.match(r'^(?:\d+[.、\)]\s*|-\s*)(.*)', line)
            if step_match:
                step = step_match.group(1).strip()
                if step:
                    steps.append(step)
        return steps

    def _execute_plan_with_subagents(
        self, steps: list[str], plan_response: str, user_input: str,
        reasoning_tokens: int = 0, reasoning_content: str = ""
    ):
        """将 [PLAN] 中的子步骤逐个分发给 SubAgent 执行

        执行结果作为 system 消息反馈到主会话，
        AI 在下一轮 ReAct 中基于结果继续（更新 Todo 等）。
        """
        # 记录 plan 响应
        plan_msg = Message(
            role="assistant",
            content=plan_response,
            token_count=self.token_counter.count_tokens(plan_response) + reasoning_tokens,
            reasoning_content=reasoning_content or None
        )
        self.session.messages.append(plan_msg)

        print(f"\n{self._c_hint}检测到子计划，共 {len(steps)} 个子步骤，交给子Agent执行...{C_RESET}")

        step_results = []
        for i, step in enumerate(steps, 1):
            print(f"\n{self._c_hint}━━━ 子步骤 {i}/{len(steps)}: {step[:60]} ━━━{C_RESET}")
            try:
                result = self.subagent_scheduler.delegate_and_run(
                    parent_name=self.agent_name,
                    task=step,
                    model_config={},
                    llm_client=self.llm_client,
                    tool_executor=self.tool_executor,
                    token_counter=self.token_counter,
                    system_prompt=(
                        f"你是一个执行子任务的Agent。\n"
                        f"用户的完整需求是: {user_input}\n\n"
                        f"你只需要完成当前子任务: {step}\n"
                        f"完成后简洁总结执行结果。"
                    )
                )
                step_results.append(f"子步骤{i} [{step}]: 完成\n{result}")
            except Exception as e:
                step_results.append(f"子步骤{i} [{step}]: 失败 - {str(e)}")

        # 汇总反馈到主会话
        summary = "\n\n".join(step_results)
        feedback = f"[子Agent执行汇总]\n{summary}"
        feedback_msg = Message(
            role="system",
            content=feedback,
            token_count=self.token_counter.count_tokens(feedback)
        )
        self.session.messages.append(feedback_msg)

        # 清理
        self.subagent_scheduler.cleanup_all()

        print(f"\n{self._c_hint}子步骤执行完毕，继续主流程...{C_RESET}")

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
                        print(f"\n{self._c_dim}思考中...{C_RESET}")
                    reasoning_buffer += content
                    sys.stdout.write(f"{self._c_dim}{content}{C_RESET}")
                    sys.stdout.flush()

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
                        print(f"\n{self._c_dim}思考完毕{C_RESET}")

                    ai_response += content
                    sys.stdout.write(content)
                    sys.stdout.flush()

            # 流式结束 — 构造结构化 tool_calls
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

            print()

        except Exception as e:
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

        # 逐个执行
        for tc in valid_calls:
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

            tool_msg = Message(
                role="tool",
                content=output,
                token_count=self.token_counter.count_tokens(output),
                tool_call_id=tc["id"]
            )
            tool_msg.metadata = {"tool_name": tc["tool"], "success": result.success}
            self.session.messages.append(tool_msg)

            if not result.success:
                fail_key = tc["tool"]
                self._failure_counts[fail_key] = self._failure_counts.get(fail_key, 0) + 1
                if self._failure_counts[fail_key] <= MAX_REFLECTION_RETRIES:
                    # 反思时传递完整错误信息（包含 traceback）
                    full_error = result.output if result.output else (result.error or "未知错误")
                    self._inject_reflection(
                        tc["tool"],
                        tc["arguments"],
                        full_error
                    )
            else:
                self._failure_counts.pop(tc["tool"], None)

    def _resolve_tool_name(self, name: str) -> Optional[str]:
        """模糊匹配工具名，返回注册名或 None"""
        tool = self.tool_executor.tool_registry.fuzzy_get(name)
        return tool.name if tool else None

    # ==================================================================
    #  自我反思
    # ==================================================================

    def _inject_reflection(self, tool_name: str, arguments: dict, error: str):
        reflection_content = REFLECTION_PROMPT.format(
            tool_name=tool_name,
            arguments=json.dumps(arguments, ensure_ascii=False),
            error=error
        )
        retry_count = self._failure_counts.get(tool_name, 0)
        remaining = MAX_REFLECTION_RETRIES - retry_count
        reflection_content += f"\n\n(剩余重试机会: {remaining})"

        hint_msg = Message(
            role="system",
            content=reflection_content,
            token_count=self.token_counter.count_tokens(reflection_content)
        )
        self.session.messages.append(hint_msg)
        print(f"\n{self._c_hint}工具执行失败，启用自我反思 (重试 {retry_count}/{MAX_REFLECTION_RETRIES})...{C_RESET}")

    # ==================================================================
    #  SubAgent 委托（由 delegate_task 工具调用）
    # ==================================================================

    def delegate_to_subagent(self, task: str, system_prompt: str = "") -> str:
        if not self.subagent_scheduler:
            return "错误: 子Agent调度器未初始化"

        result = self.subagent_scheduler.delegate_and_run(
            parent_name=self.agent_name,
            task=task,
            model_config={},
            llm_client=self.llm_client,
            tool_executor=self.tool_executor,
            token_counter=self.token_counter,
            system_prompt=system_prompt
        )

        feedback = f"[子Agent执行结果]\n任务: {task}\n结果: {result}"
        feedback_msg = Message(
            role="system",
            content=feedback,
            token_count=self.token_counter.count_tokens(feedback)
        )
        self.session.messages.append(feedback_msg)
        return result

    def on_memory_update(self, callback: Callable):
        self._on_memory_update = callback
