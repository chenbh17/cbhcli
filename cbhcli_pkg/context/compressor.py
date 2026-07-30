"""上下文压缩器 - AI驱动的上下文压缩"""
from cbhcli_pkg.core.session import Session, Message
from cbhcli_pkg.core.model import LLMClient
from cbhcli_pkg.context.token_counter import TokenCounter


def _split_at_boundary(messages: list, split_idx: int) -> tuple:
    """在安全边界处分割消息列表，确保不拆散 assistant(tool_calls) + tool 消息对。

    如果 split_idx 落在 tool 消息上，向前调整到该 tool 对应的 assistant 消息之前。
    如果 split_idx 落在带 tool_calls 的 assistant 消息上，向后调整到其所有 tool 消息之后。
    """
    if split_idx <= 0 or split_idx >= len(messages):
        return messages, []

    # 如果切分点是 tool 消息，向前找到对应的 assistant
    if messages[split_idx].role == "tool":
        # 找到这个 tool 对应的 assistant（向前搜索）
        i = split_idx - 1
        while i >= 0 and messages[i].role == "tool":
            i -= 1
        # i 现在指向非 tool 消息
        if i >= 0 and messages[i].role == "assistant" and messages[i].tool_calls:
            split_idx = i  # 从这个 assistant 之前切分

    # 如果切分点是带 tool_calls 的 assistant，向后跳过所有对应的 tool
    if (0 <= split_idx < len(messages) and
        messages[split_idx].role == "assistant" and
        messages[split_idx].tool_calls):
        # 收集这个 assistant 的 tool_call_ids
        tc_ids = {tc.get("id") for tc in messages[split_idx].tool_calls if tc.get("id")}
        j = split_idx + 1
        while j < len(messages) and messages[j].role == "tool" and messages[j].tool_call_id in tc_ids:
            j += 1
        split_idx = j  # 从 tool 消息之后切分

    return messages[:split_idx], messages[split_idx:]


class ContextCompressor:
    """上下文压缩器"""

    def __init__(self, llm_client: LLMClient, token_counter: TokenCounter):
        self.llm_client = llm_client
        self.token_counter = token_counter

    def compress(self, session: Session, target_tokens: int,
                 instructions: str = None) -> bool:
        """压缩会话上下文到目标token数

        Args:
            session: 会话对象
            target_tokens: 目标 token 数
            instructions: 可选压缩指令（如 "保留迁移方案，丢弃调试过程"），
                          透传给摘要模型引导保留/丢弃重点
        """
        system_messages = [msg for msg in session.messages if msg.role == "system"]
        conversation_messages = [msg for msg in session.messages
                                if msg.role in ["user", "assistant", "tool"]]

        if len(conversation_messages) <= 6:
            return False

        # 在安全边界处分割：保留最早2轮，最近3轮
        early_messages, rest = _split_at_boundary(conversation_messages, 4)
        middle_messages, recent_messages = _split_at_boundary(rest, len(rest) - 6)

        if not middle_messages:
            return False

        # 生成中间部分的摘要
        middle_text = "\n".join([
            f"{msg.role}: {msg.content}"
            for msg in middle_messages
            if msg.content  # 跳过空内容的 assistant（tool_calls 消息 content 可能为空）
        ])

        summary = self._generate_summary(middle_text, instructions)

        # 构建新的消息列表
        new_messages = system_messages.copy()
        new_messages.extend(early_messages)
        summary_msg = Message(
            role="system",
            content=f"[历史对话摘要]\n{summary}",
            token_count=self.token_counter.count_tokens(summary) + 20
        )
        new_messages.append(summary_msg)
        new_messages.extend(recent_messages)

        session.replace_messages(new_messages)
        return True

    def _generate_summary(self, text: str, instructions: str = None) -> str:
        system_prompt = """你是一个对话摘要专家。请总结以下对话的关键信息:
- 用户的需求和目标
- 执行的重要操作
- 修改的文件和内容
- 遇到的问题和解决方案
- 当前的任务状态

请保持简洁,但要保留所有重要的技术细节(如文件路径、命令、错误信息等)。"""

        # 用户压缩指令（保留/丢弃重点），优先级最高
        if instructions:
            system_prompt += (
                f"\n\n【用户的压缩要求（必须严格遵守）】\n{instructions}\n"
                f"用户明确要求保留的信息必须完整保留在摘要中，"
                f"用户明确要求丢弃的信息一律不得出现在摘要中。"
            )

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"请总结以下对话:\n\n{text}"}
        ]

        try:
            summary = self.llm_client.chat(messages, temperature=0.3)
            return summary
        except Exception as e:
            return f"[压缩失败: {str(e)}]\n\n{text[:500]}..."
