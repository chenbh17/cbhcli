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
import logging
from collections import defaultdict
from dataclasses import dataclass
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

    def __post_init__(self):
        if self.attachments is None:
            self.attachments = []

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


def _strip_bot_mention(content: str) -> str:
    """去除文本开头的 @机器人 提及

    QQ 群聊 @消息 格式: <@!bot_id> 或 @bot_name
    """
    # 去除 <@!xxxxx> 格式
    content = re.sub(r'<@!\d+>\s*', '', content)
    # 去除 CQ 码 @
    content = re.sub(r'\[CQ:at,qq=\d+\]\s*', '', content)
    return content.strip()


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

    def handle_event(self, payload: WSPayload):
        """处理 WebSocket DISPATCH 事件

        这是 QQBotGateway 事件回调的入口。
        """
        msg = QQBotProtocol.extract_message_content(payload)
        if msg is None:
            return  # 不是消息事件，忽略

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
        )

        # 群聊消息：去除 @机器人 前缀
        if qq_msg.message_type == 'group':
            qq_msg.content = _strip_bot_mention(qq_msg.content)

        # 空消息跳过（除非有附件）
        if not qq_msg.content.strip():
            if qq_msg.attachments:
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
                qq_msg.content = ' '.join(parts)
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
