"""QQ Bot 消息处理器

负责:
- 接收 WebSocket DISPATCH 事件
- 解析消息内容（文本/Markdown/图片/语音/文件）
- 将消息转发给 AI Agent 处理
- 将 AI 回复通过 API 发送回 QQ

仿照 openclaw-qqbot 的消息处理流程:
  QQ 消息 → 解析 → 提取上下文 → AI 处理 → 格式化回复 → 发送回 QQ
"""
import re
import json
import logging
import threading
from collections import defaultdict, OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Callable

from cbhcli_pkg.qqbot.qqbot_config import QQBotConfig
from cbhcli_pkg.qqbot.protocol import QQBotProtocol, WSPayload
from cbhcli_pkg.qqbot.api_client import QQBotAPIClient, MSG_TYPE_TEXT, MSG_TYPE_MARKDOWN

# 延迟导入 _preprocess_latex 以避免循环导入
# (message_handler → core.markdown_renderer → core.__init__ → app → qqbot_service → message_handler)
def _preprocess_latex(content):
    from cbhcli_pkg.core.markdown_renderer import _preprocess_latex as _impl
    return _impl(content)

logger = logging.getLogger(__name__)


@dataclass
class QQMessage:
    """解析后的 QQ 消息"""
    msg_id: str
    content: str
    author_id: str
    author_name: str
    timestamp: str
    event_type: str
    message_type: str     # "c2c" 或 "group"
    group_id: Optional[str] = None
    raw_payload: Optional[dict] = None
    attachments: list = None  # 附件列表
    role: str = "user"   # "user" 或 "assistant"（AI 回复） [{url, content_type, filename, size, width, height}]
    ref_content: str = ""           # 被引用消息文本（引用回复时）
    ref_attachments: list = None    # 被引用消息的附件 [{url, content_type, filename, size, width, height}]
    ref_author_name: str = ""       # 被引用消息发送者昵称
    at_bot: bool = False            # 群聊中是否 @了机器人（全量消息判定）

    def __post_init__(self):
        if self.attachments is None:
            self.attachments = []
        if self.ref_attachments is None:
            self.ref_attachments = []

    @property
    def has_images(self) -> bool:
        """是否包含图片附件"""
        return any(a.get('content_type', '').startswith('image/') for a in self.attachments)

    @property
    def image_urls(self) -> list[str]:
        """获取所有图片 URL"""
        return [a['url'] for a in self.attachments if a.get('content_type', '').startswith('image/')]

    @property
    def has_files(self) -> bool:
        """是否包含非图片文件"""
        return any(not a.get('content_type', '').startswith('image/') for a in self.attachments)


def _strip_bot_mention(content: str) -> tuple[str, bool]:
    """去除文本中的 @机器人 提及

    QQ 群聊 @消息 格式: <@!bot_id> 或 @bot_name
    提及 id 当前为 16 进制，正则放宽为任意非 > 字符（防未来格式变化）。

    Returns:
        (清理后的文本, 是否含 @机器人提及)
    """
    original = content
    # 先去除提及标签前客户端塞的裸 @/空白（手打 @ 再选人、引用回复会产生 "@<@id> 文本"，
    # 必须在 <@> 标签剥离之前执行，否则标签剥离后裸 @ 留在正文开头）
    content = re.sub(r'^[@\s]+(?=<@)', '', content)
    # 去除 <@xxxxx> / <@!xxxxx> 格式（提及 id 为机器人或普通成员，统一剥离）
    content = re.sub(r'<@!?[^>]+>\s*', '', content)
    # 去除 CQ 码 @
    content = re.sub(r'\[CQ:at,qq=\d+\]\s*', '', content)
    return content.strip(), (original != content)


def _parse_media_from_content(content: str) -> dict:
    """从消息内容中解析媒体引用

    QQ 消息中包含媒体时会有 CQ 码格式的引用。

    Returns:
        {"images": [...], "voices": [...], "files": [...]}
    """
    result = {"images": [], "voices": [], "files": []}

    # 解析图片 [CQ:image,url=xxx]
    for m in re.finditer(r'\[CQ:image,url=([^\]]+)\]', content):
        result["images"].append(m.group(1))

    # 解析语音 [CQ:record,url=xxx]
    for m in re.finditer(r'\[CQ:record,url=([^\]]+)\]', content):
        result["voices"].append(m.group(1))

    # 解析文件 [CQ:file,url=xxx]
    for m in re.finditer(r'\[CQ:file,url=([^\]]+)\]', content):
        result["files"].append(m.group(1))

    return result


class QQBotMessageHandler:
    """QQ Bot 消息处理器

    处理从 WebSocket 收到的事件，提取消息内容，
    通过回调与 AI Agent 交互。
    """

    def __init__(
        self,
        config: QQBotConfig,
        api_client: QQBotAPIClient,
        on_user_message: Optional[Callable] = None,
    ):
        """
        Args:
            config: QQ Bot 配置
            api_client: REST API 客户端
            on_user_message: 收到用户消息的回调
                async def callback(msg: QQMessage) -> str
                返回 AI 回复的内容
        """
        self.config = config
        self.api = api_client
        self._on_user_message = on_user_message

        # 对话上下文缓存（(author_id, message_type) → 最近消息列表）
        self._contexts: dict[tuple, list] = defaultdict(list)
        self._max_context = 10  # 每个对话最多保存 10 条历史

        # ── 群全量消息 @机器人 判定（v5.3.1）──
        # 群消息范围设置为"全部消息"后，同一条 @机器人 消息会同时以
        # GROUP_AT_MESSAGE_CREATE 和 GROUP_MESSAGE_CREATE 两种事件送达，
        # 需按 msg_id 去重；全量副本中 <@bot_id> 提及标签的 id 即机器人
        # 自身 id（AT 事件开头提及时可直接学习），全量消息中任意位置的
        # <@bot_id> 都算被 @。
        self._bot_mention_ids: set[str] = set()   # 机器人提及 id（学习得到）
        self._seen_msg_ids: OrderedDict = OrderedDict()  # msg_id → 已按@处理(bool)
        self._seen_max = 500                     # 去重窗口大小
        self._state_lock = threading.Lock()

        # 持久化用户注册表（v5.3.0：记录所有发过消息的用户 openid，
        # 供 qqbot_send_message 工具主动发送/查找，跨进程共享）
        from cbhcli_pkg.qqbot.qqbot_registry import QQBotUserRegistry
        self.registry = QQBotUserRegistry()

    # ── bot 提及 id 持久化（跨重启学习）──

    _MENTION_STATE_FILE = Path.home() / ".cbhcli" / "qqbot_mention_ids.json"

    def _load_mention_state(self):
        """从磁盘加载学习到的 bot 提及 id"""
        try:
            if self._MENTION_STATE_FILE.exists():
                with open(self._MENTION_STATE_FILE, 'r') as f:
                    data = json.load(f)
                ids = data.get(self.config.name)
                if isinstance(ids, list):
                    with self._state_lock:
                        self._bot_mention_ids.update(str(i) for i in ids)
        except Exception:
            pass

    def _save_mention_state(self):
        """保存学习到的 bot 提及 id 到磁盘（跨 Bot 隔离）"""
        try:
            data = {}
            if self._MENTION_STATE_FILE.exists():
                try:
                    with open(self._MENTION_STATE_FILE, 'r') as f:
                        data = json.load(f)
                except Exception:
                    data = {}
            with self._state_lock:
                data[self.config.name] = sorted(self._bot_mention_ids)
            self._MENTION_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(self._MENTION_STATE_FILE, 'w') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def _find_mention_ids(self, content: str) -> list[str]:
        """提取文本中全部 <@id> 提及 id（id 当前为 16 进制，放宽为任意非 > 字符）"""
        return [m.group(1) for m in re.finditer(r'<@!?([^>]+)>', content)]

    def learn_bot_id(self, bot_id: str):
        """学习机器人自身提及 id（qqbot_service 启动时经 get_me_info 注入）

        该 id 即群聊 @机器人 时 <@id> 标签中的提及 id，用于全量消息 @判定。
        """
        bot_id = str(bot_id or '').strip()
        if not bot_id:
            return
        with self._state_lock:
            if bot_id not in self._bot_mention_ids:
                self._bot_mention_ids.add(bot_id)
                logger.info(f"[{self.config.name}] 学习到机器人提及 id: {bot_id[:12]}...")
        self._save_mention_state()

    def _is_at_bot(self, event_type: str, content: str, mentions: list) -> bool:
        """判断群消息是否 @机器人

        - GROUP_AT_MESSAGE_CREATE：本身即 @机器人 消息，恒 True
        - GROUP_MESSAGE_CREATE（全量）：提及标签中含已学习的 bot 提及 id 才算
          （mentions 列表的 id 是消息中 @ 的用户，全量副本中含机器人自身）
        """
        if event_type == "GROUP_AT_MESSAGE_CREATE":
            return True
        mention_ids = set(self._find_mention_ids(content)) | set(mentions or [])
        if not mention_ids:
            return False
        with self._state_lock:
            return bool(mention_ids & self._bot_mention_ids)

    def handle_event(self, payload: WSPayload):
        """处理 WebSocket DISPATCH 事件

        这是 QQBotGateway 事件回调的入口。

        群全量消息（GROUP_MESSAGE_CREATE）处理规则：
        1. 仅当 @机器人 时才进入 AI 处理，其余消息忽略（旁观不回话）
        2. 与 GROUP_AT_MESSAGE_CREATE 同 msg_id 的重复推送去重
        3. 借 AT 事件学习机器人自身提及 id，供全量消息 @判定
        """
        msg = QQBotProtocol.extract_message_content(payload)
        if msg is None:
            return  # 不是消息事件，忽略

        # 惰性加载持久化的 bot 提及 id
        if not getattr(self, "_mention_state_loaded", False):
            self._mention_state_loaded = True
            self._load_mention_state()

        event_type = msg.get('event_type', '')
        content = msg.get('content', '')
        mentions = msg.get('mentions', []) or []

        # v5.3.1：事件级日志（排查 @判定/去重/引用解析问题用）
        logger.info(
            f"[{self.config.name}] 收到事件 {event_type} "
            f"msg_id={msg.get('msg_id', '')[:16]}... "
            f"author={msg.get('author_id', '')[:8]}... "
            f"content={json.dumps(content, ensure_ascii=False)[:80]} "
            f"mentions={[m[:8] + '...' if len(m) > 8 else m for m in (mentions or [])]} "
            f"atts={len(msg.get('attachments', []) or [])} "
            f"ref_atts={len(msg.get('ref_attachments', []) or [])} "
            f"ref_content={json.dumps((msg.get('ref_content') or '')[:40], ensure_ascii=False)}"
        )

        # ── 群消息：@机器人 判定 + 去重 + 提及 id 学习 ──
        is_group = msg.get('message_type') == 'group'
        is_at_bot = False
        if is_group:
            raw_content = content
            # AT 事件开头的 <@bot_id> 提及 id 即机器人自身 → 学习
            if event_type == "GROUP_AT_MESSAGE_CREATE":
                mention_ids = self._find_mention_ids(raw_content)
                if mention_ids:
                    with self._state_lock:
                        added = [i for i in mention_ids if i not in self._bot_mention_ids]
                        self._bot_mention_ids.update(mention_ids)
                    if added:
                        logger.info(f"学习到机器人提及 id: {added}")
                        self._save_mention_state()
                is_at_bot = True
            else:
                # 全量消息：任意位置含已学习 bot 提及 id 才算被 @
                is_at_bot = self._is_at_bot(event_type, raw_content, mentions)

            # 同一条消息 AT/全量双事件去重（bunqq 同款策略）：
            # - 全量副本先到且未被识别为 @机器人（提及 id 尚未学习）→ 静默丢弃，
            #   AT 副本后到时补处理
            # - 已按 @处理过 → 正常去重
            with self._state_lock:
                msg_id = msg.get('msg_id', '')
                if msg_id and msg_id in self._seen_msg_ids:
                    if event_type == "GROUP_AT_MESSAGE_CREATE" and \
                            self._seen_msg_ids[msg_id] is False:
                        # AT 副本后到补处理（此前全量副本因未识别被丢弃）
                        pass
                    else:
                        return  # 重复消息，忽略
                if msg_id:
                    self._seen_msg_ids[msg_id] = is_at_bot
                    if len(self._seen_msg_ids) > self._seen_max:
                        # 修剪最旧的一半
                        while len(self._seen_msg_ids) > self._seen_max // 2:
                            self._seen_msg_ids.popitem(last=False)

            if not is_at_bot:
                return  # 全量消息未 @机器人 → 不响应（旁观）

        # ── 持久化记录发送目标（v5.3.0，尽力而为不影响主流程）──
        try:
            if msg['message_type'] == 'c2c':
                self.registry.record_user(
                    "c2c", msg['author_id'],
                    name=msg.get('author_name', ''), bot=self.config.name,
                    content=msg.get('content', ''),
                )
            elif msg['message_type'] == 'group' and msg.get('group_id'):
                self.registry.record_user(
                    "group", msg['group_id'],
                    name="", bot=self.config.name,
                    content=msg.get('content', ''),
                    author_name=msg.get('author_name', ''),
                    author_id=msg.get('author_id', ''),
                )
        except Exception:
            pass

        # 构造 QQMessage
        qq_msg = QQMessage(
            msg_id=msg['msg_id'],
            content=msg['content'],
            author_id=msg['author_id'],
            author_name=msg['author_name'],
            timestamp=str(msg.get('timestamp', '')),
            event_type=msg['event_type'],
            message_type=msg['message_type'],
            group_id=msg.get('group_id'),
            raw_payload=payload.d,
            attachments=msg.get('attachments', []),
            ref_content=msg.get('ref_content', ''),
            ref_attachments=msg.get('ref_attachments', []) or [],
            ref_author_name=msg.get('ref_author_name', ''),
            at_bot=is_at_bot,
        )

        # 群聊消息：去除 @机器人 前缀
        if qq_msg.message_type == 'group':
            qq_msg.content, _ = _strip_bot_mention(qq_msg.content)

        # 空消息跳过（除非有附件或引用内容）
        if not qq_msg.content.strip():
            if qq_msg.attachments or qq_msg.ref_attachments:
                # 纯媒体消息：生成描述
                parts = []
                for att in qq_msg.attachments:
                    ct = att.get('content_type', '')
                    fn = att.get('filename', '')
                    if ct.startswith('image/'):
                        parts.append(f"[图片: {fn or '未命名'}]")
                    elif ct.startswith('video/'):
                        parts.append(f"[视频: {fn or '未命名'}]")
                    elif ct.startswith('audio/'):
                        parts.append(f"[语音: {fn or '未命名'}]")
                    else:
                        parts.append(f"[文件: {fn or '未命名'} ({ct})]")
                for att in qq_msg.ref_attachments:
                    ct = att.get('content_type', '')
                    fn = att.get('filename', '')
                    if ct.startswith('image/'):
                        parts.append(f"[引用图片: {fn or '未命名'}]")
                    elif ct.startswith('audio/'):
                        parts.append(f"[引用语音: {fn or '未命名'}]")
                    elif ct:
                        parts.append(f"[引用文件: {fn or '未命名'} ({ct})]")
                if qq_msg.ref_content:
                    prefix = f"引用 {qq_msg.ref_author_name}: " if qq_msg.ref_author_name else "引用: "
                    parts.append(prefix + qq_msg.ref_content)
                qq_msg.content = ' '.join(parts)
            else:
                # 引用消息也可能只有引用文本没有新文本
                if qq_msg.ref_content:
                    prefix = f"引用 {qq_msg.ref_author_name}: " if qq_msg.ref_author_name else "引用: "
                    qq_msg.content = prefix + qq_msg.ref_content
                else:
                    return

        # 保存上下文
        ctx_key = (qq_msg.author_id, qq_msg.message_type)
        self._contexts[ctx_key].append(qq_msg)
        if len(self._contexts[ctx_key]) > self._max_context:
            self._contexts[ctx_key] = self._contexts[ctx_key][-self._max_context:]

        # 处理消息
        logger.info(f"[{qq_msg.message_type}] {qq_msg.author_name}: {qq_msg.content[:50]}")

        if self._on_user_message:
            # v5.4.0+（问题3）：消息处理放入独立线程，不阻塞网关接收线程——
            # AI 任务执行期间（可达数分钟），新到达的 QQ 消息必须能被实时
            # 接收并进入调度（同人中断替换 / 异人排队）。旧实现同步调用
            # 会阻塞网关线程，新消息全部积压在 WebSocket 缓冲区中直到
            # 当前任务结束才被处理（表现为"agent 识别不到新消息"）。
            threading.Thread(
                target=self._process_message_async,
                args=(qq_msg,),
                daemon=True,
                name=f"qqmsg-{self.config.name}",
            ).start()

    def _process_message_async(self, qq_msg: QQMessage):
        """异步处理单条消息（独立线程）：AI 处理后发送回复。

        异常与失败回复逻辑与原同步实现保持一致；线程为 daemon，
        Bot 停止/进程退出时不会阻塞。
        """
        try:
            reply = self._on_user_message(qq_msg)
            if reply:
                self.send_reply(qq_msg, reply)
        except Exception as e:
            logger.error(f"消息处理失败: {e}", exc_info=True)
            # 截断错误信息，避免过长被 QQ API 拒绝
            err_msg = str(e)
            if len(err_msg) > 200:
                err_msg = err_msg[:200] + "..."
            self.send_reply(qq_msg, f"抱歉，处理消息时出错了：{err_msg}")

    def send_reply(self, msg: QQMessage, content: str):
        """发送回复消息

        自动根据原始消息类型选择发送方式：
        - C2C 消息 → 私聊回复
        - 群聊消息 → 群聊回复

        发送策略：
        1. 预处理 LaTeX 公式 → Unicode（QQ 不支持 LaTeX 渲染）
        2. 优先用 Markdown 类型发送（msg_type=2），让 QQ 客户端渲染格式
        3. Markdown 发送失败则回退纯文本（msg_type=0）

        Args:
            msg: 原始消息
            content: 回复内容
        """
        # 预处理 LaTeX → Unicode
        content = _preprocess_latex(content)

        # 优先尝试 Markdown 发送，失败回退纯文本
        # 正式环境被动回复必须传原始消息 msg_id
        sent = False
        if msg.message_type == 'c2c':
            # 先尝试 Markdown
            md_result = self.api.send_c2c_message(
                msg.author_id, content, msg_type=MSG_TYPE_MARKDOWN, msg_id=msg.msg_id
            )
            if 'error' not in md_result:
                sent = True
            else:
                logger.warning(f"Markdown 发送失败，回退纯文本: {md_result.get('error')}")
                # 回退纯文本
                result = self.api.send_c2c_message(msg.author_id, content, msg_id=msg.msg_id)
                if 'error' in result:
                    logger.error(f"发送回复失败: {result['error']}")
        elif msg.message_type == 'group' and msg.group_id:
            # 群聊：Markdown 不支持 @ 追加，先发 Markdown，失败则纯文本带 @
            md_content = content
            md_result = self.api.send_group_message(
                msg.group_id, md_content, msg_type=MSG_TYPE_MARKDOWN, msg_id=msg.msg_id
            )
            if 'error' not in md_result:
                sent = True
            else:
                logger.warning(f"群聊 Markdown 发送失败，回退纯文本: {md_result.get('error')}")
                reply_content = f"<@{msg.author_id}> {content}"
                result = self.api.send_group_message(msg.group_id, reply_content, msg_id=msg.msg_id)
                if 'error' in result:
                    logger.error(f"发送回复失败: {result['error']}")
        else:
            logger.warning(f"无法确定回复目标: {msg.message_type}")
            return

    def send_markdown_reply(self, msg: QQMessage, markdown_content: str):
        """发送 Markdown 格式回复

        Args:
            msg: 原始消息
            markdown_content: Markdown 格式内容
        """
        if msg.message_type == 'c2c':
            result = self.api.send_c2c_message(
                msg.author_id, markdown_content, msg_type=MSG_TYPE_MARKDOWN
            )
        elif msg.message_type == 'group' and msg.group_id:
            # Markdown 群聊暂不支持 @ 追加
            result = self.api.send_group_message(
                msg.group_id, markdown_content, msg_type=MSG_TYPE_MARKDOWN
            )
        else:
            logger.warning(f"无法确定 Markdown 回复目标: {msg.message_type}")
            return

        if 'error' in result:
            logger.error(f"发送 Markdown 回复失败: {result['error']}")

    def get_context(self, author_id: str, message_type: str) -> list[QQMessage]:
        """获取指定用户的对话上下文

        Args:
            author_id: 用户 openid
            message_type: "c2c" 或 "group"

        Returns:
            最近的对话消息列表
        """
        ctx_key = (author_id, message_type)
        return list(self._contexts.get(ctx_key, []))

    def clear_context(self, author_id: str, message_type: str):
        """清除指定用户的对话上下文"""
        ctx_key = (author_id, message_type)
        if ctx_key in self._contexts:
            del self._contexts[ctx_key]
