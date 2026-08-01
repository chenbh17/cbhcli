"""QQ Bot 模块 - QQ 机器人消息收发

仿照 openclaw-qqbot 架构，基于 QQ 开放平台官方 WebSocket 协议实现。

核心组件:
- QQBotConfig: 配置管理
- QQBotProtocol: WebSocket 协议编解码（OpCode 0~13）
- QQBotGateway:  WebSocket 网关连接管理
- QQBotAPIClient: REST API 客户端（发送消息等）
- QQBotMessageHandler: 消息解析与处理
- QQBotService: 主服务协调器
"""

from cbhcli_pkg.qqbot.qqbot_config import QQBotConfig
from cbhcli_pkg.qqbot.protocol import QQBotProtocol, OpCode, Intent
from cbhcli_pkg.qqbot.gateway import QQBotGateway
from cbhcli_pkg.qqbot.api_client import QQBotAPIClient
from cbhcli_pkg.qqbot.message_handler import QQBotMessageHandler
from cbhcli_pkg.qqbot.qqbot_service import QQBotService

__all__ = [
    "QQBotConfig",
    "QQBotProtocol", "OpCode", "Intent",
    "QQBotGateway",
    "QQBotAPIClient",
    "QQBotMessageHandler",
    "QQBotService",
]
