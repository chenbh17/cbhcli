"""上下文压缩器 - AI驱动的上下文压缩

设计要点（v5.1.6 优化）：
1. target_tokens 真正生效：压缩目标 = 窗口的 30%（ContextWindow.compression_target()），
   摘要生成时按预算限制 max_tokens，压缩后校验未达标则迭代降低保留轮数。
2. 摘要提示词对标 Claude Code：CRITICAL 约束（纯文本/禁工具）+ <analysis>/<summary> 双块
   + 9 章节结构化模板，减少信息丢失。
3. 压缩可撤销：压缩前自动保存原始消息到 workspace/history/compressions/，
   可通过 /undo-compress 恢复。
4. 摘要输入格式：tool 消息带工具名（[工具 xxx 结果]），assistant 带调用链标记，
   大输出截断防止摘要请求输入超长。
"""
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Optional

from cbhcli_pkg.core.session import Session, Message
from cbhcli_pkg.core.model import LLMClient
from cbhcli_pkg.context.token_counter import TokenCounter
from cbhcli_pkg.core.constants import SUMMARY_MAX_TOKENS

# 摘要输入中单条消息的最大字符数（防止超大工具输出撑爆摘要请求）
MAX_MSG_CHARS = 2000        # user/assistant 消息
MAX_TOOL_MSG_CHARS = 600    # tool 消息（工具输出通常冗长）
# 备份保留份数
MAX_BACKUPS = 10


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


def _truncate(text: str, limit: int) -> str:
    """截断长文本，保留头部，超出部分加省略标记"""
    if text is None:
        return ""
    text = str(text)
    if len(text) <= limit:
        return text
    return text[:limit] + f"...[已截断，原 {len(text)} 字符]"


# ---------------------------------------------------------------------------
# 消息序列化（备份 / 恢复用）
# ---------------------------------------------------------------------------

def _message_to_dict(msg: Message) -> dict:
    """序列化 Message 为 dict（保留 token_count / timestamp 便于展示）"""
    return {
        "role": msg.role,
        "content": msg.content,
        "token_count": msg.token_count,
        "timestamp": msg.timestamp.isoformat() if msg.timestamp else None,
        "metadata": msg.metadata,
        "tool_call_id": msg.tool_call_id,
        "tool_calls": msg.tool_calls,
        "reasoning_content": msg.reasoning_content,
        "images": msg.images,
    }


def _message_from_dict(d: dict) -> Message:
    """从 dict 反序列化 Message"""
    return Message(
        role=d.get("role", ""),
        content=d.get("content") or "",
        token_count=d.get("token_count", 0),
        tool_call_id=d.get("tool_call_id"),
        tool_calls=d.get("tool_calls"),
        reasoning_content=d.get("reasoning_content"),
        images=d.get("images"),
    )


class ContextCompressor:
    """上下文压缩器"""

    def __init__(self, llm_client: LLMClient, token_counter: TokenCounter,
                 workspace_path: Optional[str] = None):
        self.llm_client = llm_client
        self.token_counter = token_counter
        self.workspace_path = workspace_path
        # 最近一次压缩失败的原因（供调用方展示），成功时重置为 None
        self.last_error: Optional[str] = None

    # ======================================================================
    # 压缩主流程
    # ======================================================================

    def compress(self, session: Session, target_tokens: int,
                 instructions: str = None) -> bool:
        """压缩会话上下文到目标token数

        target_tokens 真正生效：摘要生成的 max_tokens 按预算限制，
        压缩后若总 token 仍超过目标，迭代降低保留轮数重试。

        Args:
            session: 会话对象
            target_tokens: 压缩后目标 token 数（应显著低于触发阈值，
                           建议用 ContextWindow.compression_target()，默认窗口 30%）
            instructions: 可选压缩指令（如 "保留迁移方案，丢弃调试过程"），
                          透传给摘要模型引导保留/丢弃重点
        """
        system_messages = [msg for msg in session.messages if msg.role == "system"]
        conversation_messages = [msg for msg in session.messages
                                if msg.role in ["user", "assistant", "tool"]]

        if len(conversation_messages) <= 6:
            return False

        before_tokens = session.get_total_tokens(self.token_counter)

        # 在安全边界处分割：保留最早2轮，最近3轮
        early_messages, rest = _split_at_boundary(conversation_messages, 4)
        middle_messages, recent_messages = _split_at_boundary(rest, len(rest) - 6)

        if not middle_messages:
            return False

        # 迭代压缩：优先 2+3 保留；若压缩后仍超目标，降级保留轮数（最近 3→2→1）重试
        keep_early = 2   # 保留最早轮数
        keep_recent = 3  # 保留最近轮数
        summary = None
        new_messages = None

        while True:
            early_messages, rest = _split_at_boundary(conversation_messages, keep_early * 2)
            middle_messages, recent_messages = _split_at_boundary(
                rest, max(0, len(rest) - keep_recent * 2))

            if not middle_messages:
                break

            # 生成中间部分的摘要（格式化为带工具名/调用链的文本）
            middle_text = self._format_middle_text(middle_messages)

            # 计算摘要预算：目标 - 保留部分（系统提示 + 最早 + 最近），至少 512
            kept_tokens = self.token_counter.count_messages_tokens(
                [m for m in (system_messages + early_messages + recent_messages)
                 if m.content or m.tool_calls])
            summary_budget = target_tokens - kept_tokens
            summary_budget = max(512, summary_budget)

            try:
                summary = self._generate_summary(middle_text, instructions,
                                                 max_tokens=summary_budget)
            except Exception as e:
                # 摘要生成失败：不替换会话消息（保持原样），记录错误并返回 False。
                # 绝不能把失败占位文本当成正常摘要塞进会话，否则会污染上下文。
                self.last_error = str(e)
                return False
            self.last_error = None

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

            # 压缩后校验：未超目标即成功；超了且还有可降级空间则继续迭代
            after_tokens = self.token_counter.count_messages_tokens(new_messages)
            if after_tokens <= target_tokens:
                break
            if keep_recent > 1:
                keep_recent -= 1
                continue
            break

        if summary is None or new_messages is None:
            return False

        # 压缩前保存原始消息（用于 /undo-compress 恢复）
        self._save_backup(session, before_tokens, after_tokens)

        session.replace_messages(new_messages)
        return True

    # ======================================================================
    # 摘要输入格式化（保留工具名/调用链，截断大输出）
    # ======================================================================

    def _format_middle_text(self, middle_messages: list) -> str:
        """将中间消息格式化为摘要输入文本

        改进：tool 消息带工具名（[工具 xxx 结果]），assistant 带调用链标记
        （[助手调用工具: a, b]），并截断超大输出防止摘要请求输入超长。
        """
        # 构建 tool_call_id -> 工具名 映射（来自 assistant 消息的 tool_calls）
        tool_names: dict = {}
        for msg in middle_messages:
            if msg.role == "assistant" and msg.tool_calls:
                for tc in msg.tool_calls:
                    if tc.get("id"):
                        tool_names[tc["id"]] = (
                            tc.get("function", {}).get("name", "unknown"))

        lines = []
        for msg in middle_messages:
            role = msg.role
            if role == "user":
                lines.append(f"[用户] {_truncate(msg.content, MAX_MSG_CHARS)}")
            elif role == "assistant":
                if msg.tool_calls:
                    names = ", ".join(
                        tc.get("function", {}).get("name", "?")
                        for tc in msg.tool_calls if tc.get("id"))
                    lines.append(f"[助手调用工具: {names}]")
                    if msg.content:
                        lines.append(f"[助手] {_truncate(msg.content, MAX_MSG_CHARS)}")
                else:
                    lines.append(f"[助手] {_truncate(msg.content, MAX_MSG_CHARS)}")
            elif role == "tool":
                name = tool_names.get(msg.tool_call_id, "unknown")
                content = msg.content or ""
                lines.append(f"[工具 {name} 结果] {_truncate(content, MAX_TOOL_MSG_CHARS)}")
            # 空内容消息（tool_calls 消息的 content 为空）跳过

        return "\n".join(lines)

    # ======================================================================
    # 摘要生成（提示词对标 Claude Code：CRITICAL 约束 + analysis/summary + 9 章节）
    # ======================================================================

    def _generate_summary(self, text: str, instructions: str = None,
                          max_tokens: Optional[int] = None) -> str:
        system_prompt = """CRITICAL: 你只能输出纯文本，禁止调用任何工具。你已拥有所需全部上下文。
你的回复必须包含两个部分：

<analysis>
先分析对话，识别：核心任务、关键决策、文件变更、用户指令、当前进度。
</analysis>

<summary>
按以下 9 个章节输出结构化摘要：
1. 任务目标：用户的核心需求
2. 整体架构：当前方案/模块设计
3. 文件变更：文件路径+关键改动内容
4. 决策：已确定的技术选型/方案
5. 已解决的问题：含关键错误信息
6. 全部用户指令：逐条列出
7. 约束：红线/限制条件
8. 当前状态：任务进行到哪一步
9. 下一步：待办事项

要求：
- 保留所有重要技术细节（文件路径、命令、错误信息、API 名）
- 丢弃中间试错过程、调试输出、冗余讨论
- 使用简洁的要点式描述，不要完整复述对话
- 没有对应内容时该章节写"无"
</summary>"""

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

        # 封顶 max_tokens：摘要预算（窗口30%-保留token）可能超过 API 的 max_tokens
        # 上限（如 131072）导致 400 invalid_parameter_error。SUMMARY_MAX_TOKENS(64k)
        # 对结构化摘要足够，且兼容主流 API。
        if max_tokens:
            max_tokens = min(max_tokens, SUMMARY_MAX_TOKENS)
        else:
            max_tokens = SUMMARY_MAX_TOKENS

        kwargs = {"temperature": 0.3, "max_tokens": max_tokens}
        # 失败时直接抛出异常（不再返回 [压缩失败...] 占位文本），由 compress() 捕获
        # 后保持会话原样并返回 False，避免失败被伪装成"压缩成功"污染上下文。
        summary = self.llm_client.chat(messages, **kwargs)
        return summary

    # ======================================================================
    # 压缩备份（/undo-compress 可撤销）
    # ======================================================================

    def _backup_dir(self) -> Optional[Path]:
        """备份目录：workspace/history/compressions/"""
        if not self.workspace_path:
            return None
        d = Path(self.workspace_path) / "history" / "compressions"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _save_backup(self, session: Session, before_tokens: int,
                     after_tokens: int) -> Optional[Path]:
        """压缩前保存原始消息到备份目录"""
        bdir = self._backup_dir()
        if not bdir:
            return None
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        file = bdir / f"{session.id}_{ts}.json"
        data = {
            "session_id": session.id,
            "agent_name": session.agent_name,
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "before_tokens": before_tokens,
            "after_tokens": after_tokens,
            "messages": [_message_to_dict(m) for m in session.messages],
        }
        try:
            file.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str))
            self._cleanup_backups()
            return file
        except Exception:
            return None

    def _cleanup_backups(self):
        """清理旧备份，只保留最近 MAX_BACKUPS 份"""
        bdir = self._backup_dir()
        if not bdir:
            return
        try:
            files = sorted(bdir.glob("*.json"), key=lambda f: f.stat().st_mtime,
                           reverse=True)
            for f in files[MAX_BACKUPS:]:
                f.unlink(missing_ok=True)
        except Exception:
            pass

    def list_backups(self) -> list[dict]:
        """列出压缩备份（按时间倒序）"""
        bdir = self._backup_dir()
        if not bdir:
            return []
        backups = []
        try:
            files = sorted(bdir.glob("*.json"), key=lambda f: f.stat().st_mtime,
                           reverse=True)
            for f in files:
                try:
                    data = json.loads(f.read_text(encoding="utf-8"))
                    backups.append({
                        "file": str(f),
                        "time": data.get("timestamp", ""),
                        "before_tokens": data.get("before_tokens", 0),
                        "after_tokens": data.get("after_tokens", 0),
                        "message_count": len(data.get("messages", [])),
                    })
                except Exception:
                    continue
        except Exception:
            pass
        return backups

    def restore_backup(self, backup_file: str, session: Session) -> bool:
        """从备份恢复会话消息（/undo-compress 用）

        恢复成功后删除该备份文件（一次性恢复，避免积累）。
        """
        try:
            data = json.loads(Path(backup_file).read_text(encoding="utf-8"))
            messages = [_message_from_dict(m) for m in data.get("messages", [])]
            if not messages:
                return False
            session.replace_messages(messages)
            session.tool_call_count = 0
            Path(backup_file).unlink(missing_ok=True)
            return True
        except Exception:
            return False
