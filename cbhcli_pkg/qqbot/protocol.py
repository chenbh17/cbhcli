"""QQ Bot WebSocket 协议编解码

基于 QQ 开放平台 WebSocket 协议 v2。
文档: https://bot.q.qq.com/wiki/develop/api-v2/dev-prepare/interface-framework/reference.html

OpCode 定义:
   0  - DISPATCH    (服务端推送事件)
   1  - HEARTBEAT   (心跳，客户端发送)
   2  - IDENTIFY    (鉴权，客户端发送)
   6  - RESUME      (恢复连接，客户端发送)
   7  - RECONNECT   (服务端通知重连)
   9  - INVALID_SESSION (无效会话)
   10 - HELLO       (连接成功，服务端发送)
   11 - HEARTBEAT_ACK (心跳回复，服务端发送)
   13 - CLIENT_STATUS (客户端状态变更)
"""
import json
import uuid
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Optional, Any


class OpCode(IntEnum):
    """WebSocket 操作码"""
    DISPATCH = 0
    HEARTBEAT = 1
    IDENTIFY = 2
    RESUME = 6
    RECONNECT = 7
    INVALID_SESSION = 9
    HELLO = 10
    HEARTBEAT_ACK = 11
    CLIENT_STATUS = 13


class Intent(IntEnum):
    """事件监听意图（位掩码）

    使用: intents = Intent.GUILDS | Intent.C2C_MESSAGE
    """
    GUILDS = 1 << 0               # 频道事件
    GUILD_MEMBERS = 1 << 1        # 频道成员
    GUILD_MESSAGES = 1 << 9       # 频道消息（仅私域机器人，实际用AT_MESSAGE）
    C2C_MESSAGE = 1 << 25         # C2C 私聊消息
    GROUP_AND_C2C_EVENT = 1 << 10  # 群聊 @消息 和 C2C 事件
    GROUP_MESSAGE = 1 << 12       # 群聊消息
    INTERACTION = 1 << 26         # 互动事件
    MESSAGE_AUDIT = 1 << 27       # 消息审核
    FORUM_EVENT = 1 << 28         # 论坛事件
    AUDIO_ACTION = 1 << 29        # 音频操作
    AT_MESSAGE = 1 << 30          # @消息（频道）

    # 常用组合
    DEFAULT = C2C_MESSAGE | GROUP_AND_C2C_EVENT  # 默认: 私聊 + 群聊@


# 事件类型名称映射
EVENT_TYPES = {
    "C2C_MESSAGE_CREATE": "C2C 私聊消息",
    "GROUP_AT_MESSAGE_CREATE": "群聊@消息",
    "AT_MESSAGE_CREATE": "频道@消息",
    "DIRECT_MESSAGE_CREATE": "频道私信",
    "MESSAGE_CREATE": "频道消息",
    "GUILD_CREATE": "频道创建",
    "GUILD_UPDATE": "频道更新",
    "GUILD_DELETE": "频道删除",
    "CHANNEL_CREATE": "子频道创建",
    "CHANNEL_UPDATE": "子频道更新",
    "CHANNEL_DELETE": "子频道删除",
    "GUILD_MEMBER_ADD": "频道成员加入",
    "GUILD_MEMBER_UPDATE": "频道成员更新",
    "GUILD_MEMBER_REMOVE": "频道成员离开",
    "MESSAGE_REACTION_ADD": "消息表情反应添加",
    "MESSAGE_REACTION_REMOVE": "消息表情反应移除",
    "INTERACTION_CREATE": "互动事件",
    "AUDIO_START": "音频开始",
    "AUDIO_FINISH": "音频结束",
    "AUDIO_ON_MIC": "音频上麦",
    "AUDIO_OFF_MIC": "音频下麦",
}


@dataclass
class WSPayload:
    """WebSocket 消息载荷"""
    op: int          # OpCode
    d: Any = None    # 数据
    s: Optional[int] = None  # 序列号（DISPATCH 事件携带）
    t: Optional[str] = None  # 事件类型（DISPATCH 事件携带）
    id: Optional[str] = None  # 请求 ID（用于关联请求和响应）


class QQBotProtocol:
    """QQ Bot WebSocket 协议编解码"""

    @staticmethod
    def decode(raw: str) -> WSPayload:
        """将接收到的 JSON 字符串解码为 WSPayload"""
        data = json.loads(raw)
        return WSPayload(
            op=data.get('op', -1),
            d=data.get('d'),
            s=data.get('s'),
            t=data.get('t'),
            id=data.get('id'),
        )

    @staticmethod
    def encode(payload: WSPayload) -> str:
        """将 WSPayload 编码为 JSON 字符串"""
        result = {'op': payload.op}
        if payload.d is not None:
            result['d'] = payload.d
        if payload.s is not None:
            result['s'] = payload.s
        if payload.t is not None:
            result['t'] = payload.t
        if payload.id is not None:
            result['id'] = payload.id
        return json.dumps(result, ensure_ascii=False)

    # ──────────── 便捷构造方法 ────────────

    @staticmethod
    def identify(token: str, intents: int = 513,
                 shard: tuple = (0, 1),
                 properties: dict = None) -> WSPayload:
        """构造 OpCode 2 IDENTIFY 鉴权消息

        Args:
            token: Bot 鉴权 Token，格式 "QQBot {access_token}"
            intents: 事件监听意图位掩码
            shard: 分片信息 (shard_id, total_shards)
            properties: 客户端属性

        Returns:
            WSPayload
        """
        if properties is None:
            import platform
            properties = {
                "$os": platform.system().lower(),
                "$browser": "cbhcli",
                "$device": "cbhcli"
            }

        return WSPayload(
            op=OpCode.IDENTIFY,
            d={
                "token": token,
                "intents": intents,
                "shard": list(shard),
                "properties": properties
            }
        )

    @staticmethod
    def heartbeat(last_seq: Optional[int] = None) -> WSPayload:
        """构造 OpCode 1 HEARTBEAT 心跳消息"""
        d = {}
        if last_seq is not None:
            d['s'] = last_seq
        return WSPayload(op=OpCode.HEARTBEAT, d=d if d else None)

    @staticmethod
    def resume(token: str, session_id: str, seq: int) -> WSPayload:
        """构造 OpCode 6 RESUME 恢复连接消息"""
        return WSPayload(
            op=OpCode.RESUME,
            d={
                "token": token,
                "session_id": session_id,
                "seq": seq
            }
        )

    @staticmethod
    def is_heartbeat_payload(payload: WSPayload) -> bool:
        """判断是否为心跳相关消息"""
        return payload.op in (OpCode.HELLO, OpCode.HEARTBEAT_ACK)

    @staticmethod
    def is_dispatch(payload: WSPayload) -> bool:
        """判断是否为事件推送"""
        return payload.op == OpCode.DISPATCH and payload.t is not None

    @staticmethod
    def is_error(payload: WSPayload) -> bool:
        """判断是否为错误消息（OpCode 9 = INVALID_SESSION）"""
        return payload.op == OpCode.INVALID_SESSION

    @staticmethod
    def event_description(event_type: str) -> str:
        """获取事件类型的中文描述"""
        return EVENT_TYPES.get(event_type, event_type)

    @staticmethod
    def extract_message_content(payload: WSPayload) -> Optional[dict]:
        """从 DISPATCH 事件中提取消息内容

        支持 C2C_MESSAGE_CREATE 和 GROUP_AT_MESSAGE_CREATE 事件。
        同时提取附件（图片、文件等）。

        Returns:
            包含以下字段的字典:
                msg_id: 消息 ID
                content: 消息文本内容
                author_id: 发送者 ID (openid)
                author_name: 发送者昵称
                timestamp: 消息时间戳
                event_type: 事件类型
                group_id: 群组 ID（仅群聊事件）
                message_type: "c2c" 或 "group"
                attachments: 附件列表 [{url, content_type, filename, size, width, height}]
        """
        if not QQBotProtocol.is_dispatch(payload):
            return None

        event_type = payload.t
        data = payload.d
        if not data:
            return None

        # 提取附件（图片/文件）
        attachments = []
        raw_attachments = data.get('attachments', [])
        for att in raw_attachments:
            attachments.append({
                'url': att.get('url', ''),
                'content_type': att.get('content_type', ''),
                'filename': att.get('filename', ''),
                'size': att.get('size', 0),
                'width': att.get('width'),
                'height': att.get('height'),
                'voice_wav_url': att.get('voice_wav_url', ''),
                'asr_refer_text': att.get('asr_refer_text', ''),
            })

        if event_type == "C2C_MESSAGE_CREATE":
            author = data.get('author', {})
            return {
                'msg_id': data.get('id', ''),
                'content': data.get('content', '').strip(),
                'author_id': author.get('id', ''),
                'author_name': author.get('username', ''),
                'timestamp': data.get('timestamp', ''),
                'event_type': event_type,
                'group_id': None,
                'message_type': 'c2c',
                'attachments': attachments,
            }

        elif event_type == "GROUP_AT_MESSAGE_CREATE":
            author = data.get('author', {})
            group_id = data.get('group_openid', data.get('group_id', ''))
            return {
                'msg_id': data.get('id', ''),
                'content': data.get('content', '').strip(),
                'author_id': author.get('id', ''),
                'author_name': author.get('username', ''),
                'timestamp': data.get('timestamp', ''),
                'event_type': event_type,
                'group_id': group_id,
                'message_type': 'group',
                'attachments': attachments,
            }

        return None
