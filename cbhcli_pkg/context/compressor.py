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

v5.2.3 压缩失忆问题修复：
5. SUMMARY_MARKER 标记 + 恢复历史会话（/resume、chat/load）保留摘要消息，
   修复"压缩过的会话恢复后摘要被丢弃 → agent 失忆"。
6. 摘要请求完全不携带 thinking/reasoning_effort（v5.2.4；v5.2.3 曾传
   thinking=disabled，但部分 API 不支持该参数直接 400，见 _generate_summary 注释）。
7. 摘要 max_tokens 再封顶到模型配置的 max_tokens（模型输出上限），
   避免对输出上限小的模型系统性 400。
8. 空/过短摘要视为失败不插入（防止空摘要顶替历史造成静默失忆）。
9. 摘要输入附上保留的 recent 消息概要，避免"当前状态/下一步"与保留消息矛盾。
10. 摘要措辞强化：明确告知模型摘要即其记忆，必须据此继续任务。

v5.4.5 压缩预算与保留策略修复：
11. 摘要预算下限 512 → 按窗口自适应（min(8192, 窗口/16)，最低 1024）。
    思考模型的 reasoning token 计入 max_tokens，512 常在思考阶段就被耗尽
    （content=null + finish_reason=length）→ 压缩必然失败。
12. 保留部分（最早2轮+最近3轮）受 token 预算约束（目标×60%），超大内容做
    头尾截断：user 永不截断、assistant 上限放宽 2 倍、tool 按上限截断。
    修复"最近几轮含超大工具输出 → kept 吃掉整个目标 → 预算被夹到 512、
    压缩后几乎没变小"。
13. 摘要请求超时 120s → 300s（可用模型配置 summary_timeout 覆盖）。
14. 模型配置可指定 summary_max_tokens；撞 400（max_tokens 超 API 上限）时
    自动减半重试，并把可用值缓存在进程内。
15. 压缩失败的错误信息带上诊断数字（目标/保留/预算），便于定位。
16. 部分 vLLM 部署把正文归到 reasoning 字段（content=null、finish=stop）时，
    从 reasoning 抢救摘要：优先 <summary> 块，其次按 9 章节关键词 + 长度的
    质量护栏；纯思考内容不会被当成摘要。
"""
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Optional

from cbhcli_pkg.core.session import Session, Message
from cbhcli_pkg.core.model import LLMClient, EmptyContentError
from cbhcli_pkg.context.token_counter import TokenCounter
from cbhcli_pkg.core.constants import (
    SUMMARY_MAX_TOKENS, SUMMARY_MIN_TOKENS, SUMMARY_MIN_TOKENS_FLOOR,
    KEEP_BUDGET_RATIO, SUMMARY_TIMEOUT,
)

# 摘要输入中单条消息的最大字符数（防止超大工具输出撑爆摘要请求）
MAX_MSG_CHARS = 2000        # user/assistant 消息
MAX_TOOL_MSG_CHARS = 600    # tool 消息（工具输出通常冗长）
# 备份保留份数
MAX_BACKUPS = 10
# 历史摘要消息的首行标记（v5.2.3）：
# 1. compress() 生成的摘要 system 消息以此开头；
# 2. /resume、chat/load 等恢复历史会话时据此保留摘要（只跳过主系统提示，
#    不能把摘要也丢了，否则恢复压缩过的会话 = agent 丢失全部早期记忆）。
SUMMARY_MARKER = "[历史对话摘要]"
# 摘要最短有效长度：短于此视为生成失败（空摘要插入会话 = agent 静默失忆）
MIN_SUMMARY_CHARS = 20
# v5.4.5：判断"reasoning 通道里其实是完整摘要"的章节关键词（质量护栏，
# 避免把纯思考内容当成摘要塞进会话）
SUMMARY_KEYWORDS = ("任务目标", "整体架构", "文件变更", "决策", "已解决",
                    "用户指令", "约束", "当前状态", "下一步")

# v5.4.5：摘要输出上限的"安全值"缓存（按 base_url + model，进程级）。
# 某些 API 的 max_tokens 硬上限比模型配置更小（400），自动减半成功后把
# 可用值记在这里，避免每次压缩都撞墙重试。
_SAFE_SUMMARY_CAP: dict = {}


def _is_max_tokens_error(exc: Exception) -> bool:
    """判断异常是否为"max_tokens 超过 API 上限"类 400"""
    text = str(exc).lower()
    if "max_tokens" not in text and "max tokens" not in text:
        return False
    return any(k in text for k in ("400", "range", "exceed", "limit", "invalid"))


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


def _turn_starts(messages: list) -> list:
    """返回每条"对话轮"的起始索引（轮 = 从一条 user 消息开始）

    v5.5.0 修复：压缩策略里的"轮"必须按 **user 消息边界** 计算，绝不能用
    "轮数 × 2 条消息"估算。一轮真实对话通常是：

        user → assistant(tool_calls) → tool → assistant(tool_calls) → tool
             → … → assistant（最终回答）

    可达十几条消息。旧实现用 `keep_recent * 2` 条消息当"最近 N 轮"，
    最近 3 "轮"实际只取到最后 6 条消息——这 6 条往往整段落在某一次工具
    循环中间（一条 user 都没有），再被 `_split_at_boundary` 调整后甚至
    变成空列表。结果压缩后模型只能看到「最早一轮 + 摘要」，用户表现为
    "压缩把最近的对话全丢了，只剩开场那一轮"。
    """
    starts = [i for i, m in enumerate(messages) if m.role == "user"]
    if not starts or starts[0] != 0:
        # 会话首条不是 user（异常历史）时把开头也当成一轮，避免丢消息
        starts.insert(0, 0)
    return starts


def _select_turns(messages: list, keep_early: int, keep_recent: int) -> tuple:
    """按「轮」切分消息列表：返回 (early, middle, recent)

    - early：最早 keep_early 轮（保留原始任务陈述及其工具链）
    - recent：最近 keep_recent 轮（**保证包含用户最近的请求**及工具链）
    - middle：中间部分（交给摘要模型）

    轮数不足（early + recent 已覆盖全部）时返回 (messages, [], [])，
    由调用方判定"没有可压缩的中间段"。
    """
    starts = _turn_starts(messages)
    if len(starts) <= keep_early + keep_recent:
        return messages, [], []
    early_end = starts[keep_early] if keep_early > 0 else 0
    recent_start = (starts[-keep_recent] if keep_recent > 0
                    else len(messages))
    if recent_start < early_end:      # 防御：两段绝不允许重叠
        recent_start = early_end
    return (messages[:early_end],
            messages[early_end:recent_start],
            messages[recent_start:])


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
        # 最近一次压缩实际使用的目标 token（v5.5.0：固定开销过大时会自适应
        # 抬高到 system + 摘要下限 + 最小保留，供日志/诊断参考）
        self.last_effective_target: Optional[int] = None

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
        # v5.4.3：区分主系统提示与历史压缩摘要。多次压缩时旧摘要不再各自
        # 独立保留（会在头部无限堆积，且严格 API 要求 system 必须在列表
        # 开头，中间/多条 system 会 400 "System message must be at the
        # beginning"）——旧摘要内容并入本次新摘要，保持仅 1 条摘要消息。
        old_summary_messages = [
            msg for msg in system_messages
            if (msg.content or "").startswith(SUMMARY_MARKER)
        ]
        main_system_messages = [
            msg for msg in system_messages
            if not (msg.content or "").startswith(SUMMARY_MARKER)
        ]
        conversation_messages = [msg for msg in session.messages
                                if msg.role in ["user", "assistant", "tool"]]

        # v5.5.1：每次压缩先清空上一次的失败原因（否则 /comp 会显示旧错误）
        self.last_error = None

        if len(conversation_messages) <= 6:
            self.last_error = (
                f"当前只有 {len(conversation_messages)} 条对话消息"
                f"（少于 7 条），没有可压缩的历史")
            return False

        before_tokens = session.get_total_tokens(self.token_counter)

        # 迭代压缩：优先 2+3 保留；若压缩后仍超目标，降级保留轮数重试。
        # v5.5.0：轮数按 user 消息边界计算（见 _turn_starts 注释）——
        # 旧实现按"轮数×2 条消息"估算，最近几轮常整段落在工具循环中间，
        # 压缩后最近的用户请求全部消失，只剩最早一轮。
        keep_early = 2   # 保留最早轮数
        keep_recent = 3  # 保留最近轮数
        summary = None
        new_messages = None
        after_tokens = before_tokens

        # v5.5.1：轮数不多但上下文很长时也要能压缩——让出"最早轮"腾出中间段。
        # 旧逻辑 `keep_early + keep_recent` 覆盖全部轮就返回 False，用户明明有
        # 几十万 token 的上下文，/comp 却提示"上下文较短,无需压缩"。
        _total_turns = len(_turn_starts(conversation_messages))
        if _total_turns <= keep_recent:
            self.last_error = (
                f"当前会话只有 {_total_turns} 轮对话（保留最近 {keep_recent} 轮"
                f"就已覆盖全部），没有可压缩的中间历史")
            return False
        # 轮数不足时收缩"最早轮"，保证至少有 1 轮中间段可被摘要
        keep_early = min(keep_early, max(0, _total_turns - keep_recent - 1))

        # v5.5.0：压缩目标不能小于「不可压缩的固定开销 + 摘要下限 + 最小保留」。
        # 系统提示（长期记忆/工具指南/usage）本身可能就超过窗口 30%
        # （实测某会话 system≈56k tokens，而 target=窗口30%=38k）——此时旧逻辑
        # 永远判"超目标"，把保留轮一路压到只剩最后 1 条工具消息，用户表现为
        # "压缩后最近的对话全没了"。这里自适应抬高目标（仍显著低于 80% 触发点）。
        system_tokens = self.token_counter.count_messages_tokens(
            [m for m in system_messages if m.content or m.tool_calls])
        # 最小保留额度：与压缩目标成比例（窗口 128k → 约 19k tokens），
        # 保证"至少能留住最近几轮"而不被系统提示的开销挤成 0。
        min_keep_tokens = max(4000, min(24000, target_tokens // 2))
        effective_target = max(
            target_tokens,
            system_tokens + self._min_summary_budget() + min_keep_tokens)
        # 安全上限：绝不把目标抬到接近 80% 触发阈值（否则压缩完立即又触发，
        # 进入"压缩→再压缩"的死循环）。
        _limit = int(getattr(self.llm_client, "context_limit", 0) or 0)
        if _limit > 0:
            _safe_cap = int(_limit * 0.7)
            if _safe_cap > system_tokens + 2000:
                effective_target = min(effective_target, _safe_cap)
        self.last_effective_target = effective_target

        # v5.4.5：保留部分也受 token 预算约束（KEEP_BUDGET_RATIO）。
        # 旧实现按"消息条数"保留，一旦最近几轮含超大工具输出（单条几万 token），
        # kept 吃掉整个压缩目标 → 摘要预算被夹到下限、压缩后几乎没变小。
        # 这里对保留消息的超大内容做"头+尾"截断（只作用于进入新会话的副本，
        # 压缩失败时原会话不受影响）。
        # v5.5.0：预算按"可用空间"（目标 − 系统固定开销）分配，不再用整个目标。
        keep_budget = max(
            int((effective_target - system_tokens) * KEEP_BUDGET_RATIO), 2000)

        while True:
            # v5.5.0：按「轮」（user 消息边界）切分——recent 始终包含用户
            # 最近的请求及其完整工具调用链，不会被工具循环边界吃空。
            early_all, middle_messages, recent_all = _select_turns(
                conversation_messages, keep_early, keep_recent)

            if not middle_messages:
                break

            # 每条消息的截断上限随"保留轮的数量"自适应：保留的消息越多，
            # 单条越要少占（总额不动，避免 recent 把预算吃光）。
            # 下限 200 tokens：截断太狠会让最近轮只剩标题，反而丢关键信息。
            n_kept = max(1, len(early_all) + len(recent_all))
            per_msg_budget = max(200, keep_budget // n_kept)
            early_messages = self._cap_kept_messages(early_all, per_msg_budget)
            recent_messages = self._cap_kept_messages(recent_all, per_msg_budget)

            # 生成中间部分的摘要（格式化为带工具名/调用链的文本）。
            # v5.2.3：同时附上保留的 recent 消息概要，让摘要模型知道哪些内容被原样保留，
            # 避免摘要的"当前状态/下一步"与保留消息矛盾（如声称已完成的任务"待完成"）。
            middle_text = self._format_middle_text(middle_messages)
            recent_text = (self._format_middle_text(recent_messages)
                           if recent_messages else "")

            # 保留部分（系统提示 + 最早 + 最近）token
            kept_tokens = self.token_counter.count_messages_tokens(
                [m for m in (system_messages + early_messages + recent_messages)
                 if m.content or m.tool_calls])
            # v5.5.0：降级判断只看**可降级的轮**（early + recent）。
            # 系统提示（记忆/工具指南/usage）是不可压缩的固定开销，一旦它本身
            # 就超过 keep_budget，旧逻辑会把保留轮直接压到最低——压缩后只剩
            # 最后 1 条工具消息。这里把系统提示排除在"轮预算"之外
            # （system_tokens 已在循环外算好）。
            kept_turn_tokens = kept_tokens - system_tokens
            turn_budget = max(
                2000, min(keep_budget, effective_target - system_tokens))

            # 保留轮超预算时降级保留轮数（移出的消息进入摘要范围），
            # 避免摘要预算被保留部分吃光。
            # v5.5.0：**优先牺牲最早轮**——早期内容已经进入（旧）摘要，
            # 而最近对话是用户最在意、摘要最容易失真的部分，必须保住。
            if kept_turn_tokens > turn_budget:
                if keep_early > 0:
                    keep_early -= 1
                    continue
                if keep_recent > 1:
                    keep_recent -= 1
                    continue

            # 摘要预算：目标 - 保留部分；下限按窗口自适应（v5.4.5：512 → 8k）
            summary_budget = max(self._min_summary_budget(),
                                 effective_target - kept_tokens)

            try:
                summary = self._generate_summary(middle_text, instructions,
                                                 max_tokens=summary_budget,
                                                 recent_text=recent_text)
            except Exception as e:
                # 摘要生成失败：不替换会话消息（保持原样），记录错误并返回 False。
                # 绝不能把失败占位文本当成正常摘要塞进会话，否则会污染上下文。
                # v5.4.5：错误信息带上诊断数字（目标/保留/预算），否则只看到
                # "max_tokens=512" 无法判断是预算被挤到下限还是模型输出上限太小。
                self.last_error = (
                    f"{e}（本次压缩：目标 {effective_target:,}，"
                    f"保留 {kept_tokens:,}，摘要预算 {summary_budget:,}）")
                return False
            # v5.2.3：空/过短摘要视为失败。空摘要插入会话会顶替掉全部中间历史，
            # 造成 agent 静默失忆（思考模型耗尽 max_tokens 等场景可能返回空内容）。
            if not summary or len(summary.strip()) < MIN_SUMMARY_CHARS:
                self.last_error = "摘要生成结果为空或过短，为防止丢失记忆已放弃本次压缩"
                return False
            self.last_error = None

            # 构建新的消息列表
            # v5.4.3：开头只放主系统提示（不含历史摘要）；旧摘要内容并入
            # 本次新摘要消息（见下方），整个会话列表始终保持"1 条主系统
            # 提示 + 至多 1 条摘要 system 消息"，避免多次压缩后 system
            # 消息无限堆积（严格 API 要求 system 必须在列表开头，中间
            # 位置出现会 400 "System message must be at the beginning"）。
            # v5.5.1：**摘要消息必须紧跟主系统提示**（以前 append 在 early
            # 之后 = 对话中间，严格 API 直接 400）。顺序固定为：
            #   [主系统提示...] + [摘要 system] + [最早轮] + [最近轮]
            # v5.2.3：摘要措辞强化——明确告知模型"这是你的记忆，必须据此继续任务"。
            if old_summary_messages:
                # v5.4.3：旧摘要并入新摘要（原文保留，记忆不丢失），
                # 防止多次压缩累积出多条 system 摘要消息
                old_parts = "\n\n".join(
                    (m.content or "").strip()
                    for m in old_summary_messages if (m.content or "").strip())
                summary_content = (
                    f"{SUMMARY_MARKER}\n"
                    "【重要】早期对话历史已被压缩为以下摘要，这份摘要就是你对早期对话的完整记忆。\n"
                    "你必须依据摘要中的信息（任务目标、文件路径、决策、当前进度、下一步计划）继续任务，\n"
                    "禁止声称自己失忆、被压缩、不记得任务内容或进度。\n\n"
                    f"【旧压缩摘要（更早的历史，同样作为你的记忆保留）】\n"
                    f"{old_parts}\n\n"
                    f"【本次新压缩摘要（更近的历史）】\n{summary}"
                )
            else:
                summary_content = (
                    f"{SUMMARY_MARKER}\n"
                    "【重要】早期对话历史已被压缩为以下摘要，这份摘要就是你对早期对话的完整记忆。\n"
                    "你必须依据摘要中的信息（任务目标、文件路径、决策、当前进度、下一步计划）继续任务，\n"
                    "禁止声称自己失忆、被压缩、不记得任务内容或进度。\n\n"
                    f"{summary}"
                )
            summary_msg = Message(
                role="system",
                content=summary_content,
                token_count=self.token_counter.count_tokens(summary_content) + 20
            )
            # v5.5.1：所有 system 消息必须在最前——摘要紧跟主系统提示
            new_messages = main_system_messages.copy()
            new_messages.append(summary_msg)
            new_messages.extend(early_messages)
            new_messages.extend(recent_messages)

            # 压缩后校验：未超目标即成功；超了且还有可降级空间则继续迭代
            after_tokens = self.token_counter.count_messages_tokens(new_messages)
            if after_tokens <= effective_target:
                break
            # v5.5.0：同样优先牺牲最早轮（已进入摘要），保住最近对话
            if keep_early > 0:
                keep_early -= 1
                continue
            if keep_recent > 1:
                keep_recent -= 1
                continue
            break

        if summary is None or new_messages is None:
            # v5.5.1：给出具体原因，避免 UI 统一显示"上下文较短,无需压缩"
            if not self.last_error:
                self.last_error = (
                    "没有可压缩的中间历史（保留轮已覆盖全部对话）")
            return False

        # v5.4.5：压缩后反而变大（会话本身很小 / 摘要比原内容还长）时放弃，
        # 避免"压缩"把上下文撑得更大（正常触发压缩时不会发生，仅作防御）。
        if after_tokens >= before_tokens:
            self.last_error = (
                f"压缩后未变小（{before_tokens:,} → {after_tokens:,} tokens），"
                f"已放弃本次压缩")
            return False

        # 压缩前保存原始消息（用于 /undo-compress 恢复）
        self._save_backup(session, before_tokens, after_tokens)

        session.replace_messages(new_messages)
        return True

    # ======================================================================
    # 预算与保留消息裁剪（v5.4.5）
    # ======================================================================

    def _min_summary_budget(self) -> int:
        """摘要输出预算下限（v5.4.5：512 → 按窗口自适应）

        - 128k / 256k 及以上窗口 → 8192（SUMMARY_MIN_TOKENS 封顶）
        - 小窗口自动缩小（窗口/16），最低 SUMMARY_MIN_TOKENS_FLOOR=1024
        理由：思考模型的 reasoning token 计入 max_tokens，512 常在思考阶段
        就被耗尽（content=null + finish_reason=length），压缩必然失败。
        """
        limit = int(getattr(self.llm_client, "context_limit", 0) or 0)
        if limit <= 0:
            return SUMMARY_MIN_TOKENS
        return max(SUMMARY_MIN_TOKENS_FLOOR,
                   min(SUMMARY_MIN_TOKENS, limit // 16))

    def _cap_kept_messages(self, messages: list, per_msg_tokens: int) -> list:
        """对保留消息的超大内容做头尾截断，返回新列表（不修改原 Message）

        v5.4.5（A 方案）：按角色分级，避免误伤关键信息
        - user：永不截断（用户指令原样保留）
        - assistant：上限放宽 2 倍；同时截断 reasoning_content 与
          tool_calls 参数（思考模型/大脚本调用可能单条几万 token）
        - tool：按 per_msg_tokens 截断（超大输出基本都在这里）

        v5.5.0：**assistant 的 reasoning_content 直接丢弃**。思考过程对继续
        任务的价值最低、占用却最高（单条可上万 token），留着会把"最近几轮"
        的预算吃光，导致整轮被降级掉——丢思考、保正文/工具链更划算。

        只影响压缩成功后进入新会话的副本；压缩失败时原会话保持原样。
        """
        capped = []
        for msg in messages:
            if msg.role == "user":
                capped.append(msg)
                continue
            limit = (per_msg_tokens * 2 if msg.role == "assistant"
                     else per_msg_tokens)
            content = msg.content or ""
            new_content = self._cap_text(content, limit)
            new_reasoning = msg.reasoning_content
            new_tool_calls = msg.tool_calls
            if msg.role == "assistant":
                if new_reasoning:
                    new_reasoning = None      # v5.5.0：丢弃思考过程（见 docstring）
                if new_tool_calls:
                    new_tool_calls = self._cap_tool_calls(new_tool_calls, limit)
            if (new_content == content
                    and new_reasoning == msg.reasoning_content
                    and new_tool_calls is msg.tool_calls):
                capped.append(msg)
                continue
            capped.append(Message(
                role=msg.role,
                content=new_content,
                metadata=msg.metadata,
                tool_call_id=msg.tool_call_id,
                tool_calls=new_tool_calls,
                reasoning_content=new_reasoning,
                images=msg.images,
            ))
        return capped

    def _cap_text(self, text: str, token_limit: int) -> str:
        """文本按 token 上限做头尾截断（保留头 70% + 尾 30%）"""
        tokens = self.token_counter.count_tokens(text)
        if tokens <= token_limit:
            return text
        # token→字符按实际比例换算。下限 200 字符（v5.5.0：400 → 200）：
        # 最近轮消息条数多（一轮常常十几条），下限太高会让 kept 整体超预算，
        # 从而把保留轮数一路压到 1 轮——宁可每段留短摘要，也要留住"最近几轮"。
        keep_chars = max(200, int(len(text) * token_limit / max(tokens, 1)))
        head_len = int(keep_chars * 0.7)
        tail_len = max(0, keep_chars - head_len)
        omitted = max(0, len(text) - head_len - tail_len)
        return (
            text[:head_len]
            + f"\n\n...[压缩时截断 {omitted:,} 字符]...\n\n"
            + (text[-tail_len:] if tail_len else "")
        )

    def _cap_tool_calls(self, tool_calls: list, token_limit: int) -> list:
        """截断工具调用参数（保留 id/name，保证与 tool 消息的配对不失效）

        v5.5.1 修复（重要）：旧实现按**字符位置**直接切 args 字符串再拼
        `...[N 字符已截断]`，会把 JSON 切断在字符串/转义序列中间
        （例如 `\任` 被切成 `\\u...[已截断]`）→ 变成**非法 JSON**。
        这份会话一旦再发送，API 解析 tool_calls 参数就会 400
        `Invalid \\uXXXX escape`（用户实测）。

        现在改为**结构化截断**：
        1. 能解析成 JSON 的：递归截断所有字符串值 → 重新 json.dumps（合法）；
        2. 解析不了的：整体替换为合法占位 JSON（绝不拼接半个原文）。
        """
        try:
            raw = json.dumps(tool_calls, ensure_ascii=False)
        except Exception:
            return tool_calls
        if self.token_counter.count_tokens(raw) <= token_limit:
            return tool_calls
        # v5.5.0：下限 200 → 100 字符。工具参数常常很长（脚本/JSON），
        # 保留过多会让"最近几轮"整体超预算而被降级掉几轮。
        per_call = max(100, token_limit // max(1, len(tool_calls)))
        capped = []
        for tc in tool_calls:
            tc = dict(tc)
            fn = dict(tc.get("function") or {})
            args = fn.get("arguments")
            if not isinstance(args, str):
                try:
                    args = json.dumps(args, ensure_ascii=False)
                except Exception:
                    args = str(args)
            if args and self.token_counter.count_tokens(args) > per_call:
                fn["arguments"] = self._truncate_json_args(args, per_call)
            tc["function"] = fn
            capped.append(tc)
        return capped

    def _truncate_json_args(self, args: str, token_limit: int) -> str:
        """把工具参数截断成**合法 JSON 字符串**（v5.5.1）

        优先"结构化截断"（保留字段名与结构，只缩短过长的字符串值）；
        无法解析时返回合法占位 JSON——绝不返回半截原文。
        """
        try:
            parsed = json.loads(args)
        except Exception:
            parsed = None
        if isinstance(parsed, (dict, list)):
            tokens = max(1, self.token_counter.count_tokens(args))
            char_budget = max(60, int(len(args) * token_limit / tokens))
            try:
                capped = self._truncate_json_strings(parsed, char_budget)
                return json.dumps(capped, ensure_ascii=False)
            except Exception:
                pass
        keep = max(60, min(len(args), 200))
        return json.dumps(
            {"_truncated": True,
             "_note": "工具参数过长，压缩时已截断",
             "_prefix": args[:keep]},
            ensure_ascii=False)

    def _truncate_json_strings(self, obj, limit: int, depth: int = 0):
        """递归截断 JSON 结构里的字符串值（保持结构合法）"""
        if depth > 12:
            return obj
        if isinstance(obj, str):
            if len(obj) <= limit:
                return obj
            return obj[:limit] + f"…[截断 {len(obj) - limit} 字符]"
        if isinstance(obj, list):
            return [self._truncate_json_strings(x, limit, depth + 1)
                    for x in obj]
        if isinstance(obj, dict):
            return {k: self._truncate_json_strings(v, limit, depth + 1)
                    for k, v in obj.items()}
        return obj

    def _client_key(self) -> tuple:
        """摘要请求安全上限缓存的键（同一服务+模型共享）"""
        return (getattr(self.llm_client, "base_url", ""),
                getattr(self.llm_client, "model_name", ""))

    def _summary_from_reasoning(self, reasoning: str) -> Optional[str]:
        """从 reasoning 通道抢救摘要（v5.4.5）

        背景：部分 vLLM 部署（--reasoning-parser qwen3）会把正文整段归到
        reasoning 字段——content=null、finish_reason=stop，但摘要其实完整
        （实测思考内容里含 <summary> 块和 9 章节）。

        质量护栏（宁可不压缩，也不把"纯思考"当摘要塞进会话）：
        - 优先提取 <summary>...</summary> 块；
        - 否则要求命中 ≥2 个 9 章节关键词、且长度足够（≥100 字符）；
        - 都不满足返回 None。
        """
        text = (reasoning or "").strip()
        if len(text) < MIN_SUMMARY_CHARS:
            return None
        m = re.search(r"<summary>(.*?)</summary>", text, re.S | re.I)
        if m and m.group(1).strip():
            return m.group(1).strip()
        hits = sum(1 for k in SUMMARY_KEYWORDS if k in text)
        if hits >= 2 and len(text) >= MIN_SUMMARY_CHARS * 5:
            text = re.sub(r"<analysis>.*?</analysis>", "", text,
                          flags=re.S | re.I).strip()
            if len(text) >= MIN_SUMMARY_CHARS:
                return text
        return None

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
                          max_tokens: Optional[int] = None,
                          recent_text: str = "") -> str:
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
- 若提供了"原样保留的最近消息"，第 8/9 章节必须与其保持一致：保留消息中已完成的工作不得写成"待完成"，也不得把保留消息之外的内容声称为"用户最后请求"
</summary>"""

        # 用户压缩指令（保留/丢弃重点），优先级最高
        if instructions:
            system_prompt += (
                f"\n\n【用户的压缩要求（必须严格遵守）】\n{instructions}\n"
                f"用户明确要求保留的信息必须完整保留在摘要中，"
                f"用户明确要求丢弃的信息一律不得出现在摘要中。"
            )

        # v5.2.3：附上保留的 recent 消息概要（不在摘要范围内），
        # 供摘要模型判断真实进度，避免"当前状态/下一步"与保留消息矛盾。
        user_content = f"请总结以下对话:\n\n{text}"
        if recent_text:
            user_content += (
                "\n\n【以下最近消息被原样保留，不在总结范围内，"
                "仅供你判断当前状态和下一步时参考】\n" + recent_text
            )

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content}
        ]

        # 封顶 max_tokens：摘要预算（窗口30%-保留token）可能超过 API 的 max_tokens
        # 上限（如 131072）导致 400 invalid_parameter_error。SUMMARY_MAX_TOKENS(64k)
        # 对结构化摘要足够，且兼容主流 API。
        # v5.2.3：再封顶到模型配置的 max_tokens（若用户设置了，通常就是该模型的
        # 输出上限，如 32000），否则摘要请求对这些模型会系统性 400 失败。
        # v5.4.5：支持模型配置 summary_max_tokens 单独指定摘要输出上限；
        # 并复用"撞过 400 后减半得到的进程级安全值"。
        cap = SUMMARY_MAX_TOKENS
        model_cfg = getattr(self.llm_client, "model_config", {}) or {}
        model_max = model_cfg.get("max_tokens") or getattr(
            self.llm_client, "max_tokens", None)
        if model_max:
            cap = min(cap, int(model_max))
        if model_cfg.get("summary_max_tokens"):
            cap = min(cap, int(model_cfg["summary_max_tokens"]))
        if max_tokens:
            max_tokens = min(max_tokens, cap)
        else:
            max_tokens = cap
        safe_cap = _SAFE_SUMMARY_CAP.get(self._client_key())
        if safe_cap:
            max_tokens = min(max_tokens, int(safe_cap))

        # v5.4.5：摘要请求超时可覆盖（模型配置 summary_timeout，默认 300s）
        timeout = model_cfg.get("summary_timeout") or SUMMARY_TIMEOUT
        kwargs = {"temperature": 0.3, "max_tokens": max_tokens,
                  "skip_thinking": True, "timeout": timeout}
        # （v5.2.3 历史注记：当时曾对摘要请求显式关闭思考模式，v5.2.4 已改为
        # 完全不带思考参数，见下方 v5.2.4 注释。当时关思考的理由：）
        # 1. 思考模型的 reasoning token 计入 max_tokens 预算，思考耗尽预算时
        #    content=None → 压缩失败（摘要这种简单任务不需要深度思考）；
        # 2. 高强度思考 + 非流式请求容易超过 API_TIMEOUT(120s) 超时失败。
        # v5.2.4：不再传 thinking={"type":"disabled"}——部分 API（如智谱 GLM）
        # 不支持 thinking.type=disabled，会直接 400 报错：
        #   "thinking.type `disabled` is not supported by this model"
        # 改为上面的 skip_thinking=True 哨兵（由 LLMClient.chat() 处理）：
        # 无论模型配置了什么 thinking/reasoning_effort，摘要请求一律不带这两个参数。
        # v5.4.5：本版本不做"关闭思考"的探测/重试（用户要求），只通过提高预算下限
        # + 阶梯翻倍 + 超时放宽来兼容思考模型；空正文由 MIN_SUMMARY_CHARS 拦截，
        # 绝不把失败伪装成压缩成功。
        # v5.4.5 迭代：部分 vLLM 部署（--reasoning-parser qwen3）会把正文整段归到
        # reasoning 字段，content=null 但摘要其实完整（实测思考内容里含 <summary>）。
        # 此时从 reasoning 抢救（有质量护栏，见 _summary_from_reasoning）。
        # 失败时直接抛出异常（不再返回 [压缩失败...] 占位文本），由 compress() 捕获
        # 后保持会话原样并返回 False，避免失败被伪装成"压缩成功"污染上下文。

        def _call_with(mt: int) -> str:
            return self.llm_client.chat(
                messages, **{**kwargs, "max_tokens": mt})

        def _rescue(exc):
            """空正文但思考通道里有完整摘要时抢救（非 max_tokens 问题）"""
            if isinstance(exc, EmptyContentError):
                return self._summary_from_reasoning(exc.reasoning)
            return None

        try:
            return _call_with(max_tokens)
        except Exception as e:
            rescued = _rescue(e)
            if rescued:
                return rescued
            # v5.4.5：某些 API 的 max_tokens 硬上限比模型配置更小（400），
            # 自动减半重试并把可用值缓存到本进程，避免每次压缩都撞墙。
            if not _is_max_tokens_error(e):
                raise
            last_error = e
            while max_tokens > 1024:
                max_tokens = max(1024, max_tokens // 2)
                try:
                    summary = _call_with(max_tokens)
                    _SAFE_SUMMARY_CAP[self._client_key()] = max_tokens
                    return summary
                except Exception as e2:
                    rescued = _rescue(e2)
                    if rescued:
                        return rescued
                    last_error = e2
                    if not _is_max_tokens_error(e2):
                        raise
            raise last_error

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
