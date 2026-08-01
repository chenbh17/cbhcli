"""QQ Bot 主服务协调器

仿照 openclaw-qqbot 的架构，整合 Gateway + API Client + Message Handler，
提供统一的 QQ Bot 服务接口。

职责:
- Bot 生命周期管理（启动/停止/重启）
- 多个 Bot 实例管理
- 状态监控
- 对外发送消息接口

用法:
    service = QQBotService()
    service.start_bot("default")
    # ... Bot 在线收发消息 ...
    service.stop_bot("default")
"""
import logging
import threading
import time
from typing import Optional, Callable

from cbhcli_pkg.qqbot.qqbot_config import QQBotConfig, QQBotConfigManager
from cbhcli_pkg.qqbot.gateway import QQBotGateway
from cbhcli_pkg.qqbot.api_client import QQBotAPIClient
from cbhcli_pkg.qqbot.message_handler import QQBotMessageHandler, QQMessage

logger = logging.getLogger(__name__)


class BotInstance:
    """单个 Bot 实例的运行时状态"""

    def __init__(self, config: QQBotConfig):
        self.config = config
        self.name = config.name
        self.api_client = QQBotAPIClient(config)
        self.message_handler: Optional[QQBotMessageHandler] = None
        self.gateway: Optional[QQBotGateway] = None
        self._gateway_thread: Optional[threading.Thread] = None

        # 状态
        self.status = "stopped"  # stopped, starting, running, error
        self.error_message: Optional[str] = None
        self.started_at: Optional[float] = None

    @property
    def is_running(self) -> bool:
        return self.status == "running" and self.gateway is not None and self.gateway.is_connected


class QQBotService:
    """QQ Bot 主服务

    管理多个 QQ Bot 实例的生命周期。

    使用示例:
        service = QQBotService()

        # 配置 Bot
        config = QQBotConfig(name="mybot", appId="xxx", appSecret="yyy")
        service.config_manager.add(config)

        # 设置消息回调
        def on_message(qq_msg: QQMessage) -> str:
            return f"收到: {qq_msg.content}"

        service.set_message_callback("mybot", on_message)

        # 启动
        service.start_bot("mybot")

        # 查询状态
        print(service.get_status("mybot"))

        # 发送消息
        service.send_message("mybot", "c2c", "user_openid", "Hello!")

        # 停止
        service.stop_bot("mybot")
    """

    def __init__(self, config_path: Optional[str] = None):
        """
        Args:
            config_path: 配置文件路径（默认 ~/.cbhcli/config.json）
        """
        self.config_manager = QQBotConfigManager(
            config_path=None if config_path is None else __import__('pathlib').Path(config_path)
        )
        self._instances: dict[str, BotInstance] = {}
        self._callbacks: dict[str, Callable] = {}  # bot_name → callback

    # ════════════════════════════════════════════════
    # Bot 生命周期
    # ════════════════════════════════════════════════

    def start_bot(self, bot_name: str) -> bool:
        """启动指定的 QQ Bot

        Args:
            bot_name: Bot 名称

        Returns:
            True 如果启动成功
        """
        config = self.config_manager.get(bot_name)
        if not config:
            logger.error(f"Bot '{bot_name}' 不存在")
            return False

        if not config.enabled:
            logger.warning(f"Bot '{bot_name}' 已禁用")
            return False

        # 如果已经在运行中，先停止
        if bot_name in self._instances and self._instances[bot_name].is_running:
            logger.warning(f"Bot '{bot_name}' 已在运行中")
            return True

        # 创建实例
        instance = BotInstance(config)
        instance.status = "starting"

        # 创建消息处理器
        callback = self._callbacks.get(bot_name)
        instance.message_handler = QQBotMessageHandler(
            config=config,
            api_client=instance.api_client,
            on_user_message=callback,
        )

        # 创建网关
        instance.gateway = QQBotGateway(
            config=config,
            on_event=instance.message_handler.handle_event,
            on_ready=lambda sid: self._on_bot_ready(bot_name),
            on_disconnect=lambda reason: self._on_bot_disconnect(bot_name, reason),
        )

        # 连接网关
        if instance.gateway.connect():
            instance.status = "running"
            instance.started_at = time.time()
            self._instances[bot_name] = instance

            # 在后台线程运行
            instance._gateway_thread = threading.Thread(
                target=instance.gateway.run,
                daemon=True,
                name=f"qqbot-{bot_name}"
            )
            instance._gateway_thread.start()

            logger.info(f"✓ Bot '{bot_name}' 启动成功")
            return True
        else:
            instance.status = "error"
            instance.error_message = "网关连接失败"
            self._instances[bot_name] = instance
            logger.error(f"✗ Bot '{bot_name}' 启动失败")
            return False

    def stop_bot(self, bot_name: str):
        """停止指定的 QQ Bot"""
        instance = self._instances.get(bot_name)
        if instance and instance.gateway:
            instance.gateway.disconnect()
            instance.status = "stopped"
            logger.info(f"Bot '{bot_name}' 已停止")

    def restart_bot(self, bot_name: str) -> bool:
        """重启指定的 QQ Bot"""
        self.stop_bot(bot_name)
        time.sleep(1)
        return self.start_bot(bot_name)

    def start_all_enabled(self):
        """启动所有启用的 Bot"""
        for config in self.config_manager.list_all():
            if config.enabled:
                self.start_bot(config.name)

    def stop_all(self):
        """停止所有 Bot"""
        for bot_name in list(self._instances.keys()):
            self.stop_bot(bot_name)

    # ════════════════════════════════════════════════
    # 消息发送
    # ════════════════════════════════════════════════

    def send_message(
        self,
        bot_name: str,
        target_type: str,
        target_id: str,
        content: str,
    ) -> dict:
        """通过指定 Bot 发送消息

        Args:
            bot_name: Bot 名称
            target_type: "c2c" 或 "group"
            target_id: 目标用户/群 openid
            content: 消息内容

        Returns:
            API 响应 JSON
        """
        instance = self._instances.get(bot_name)
        if not instance or not instance.api_client:
            return {"error": f"Bot '{bot_name}' 未就绪"}

        api = instance.api_client
        if target_type == "c2c":
            return api.send_c2c_message(target_id, content)
        elif target_type == "group":
            return api.send_group_message(target_id, content)
        else:
            return {"error": f"不支持的目标类型: {target_type}"}

    def send_markdown(
        self,
        bot_name: str,
        target_type: str,
        target_id: str,
        markdown: str,
    ) -> dict:
        """通过指定 Bot 发送 Markdown 消息"""
        instance = self._instances.get(bot_name)
        if not instance or not instance.api_client:
            return {"error": f"Bot '{bot_name}' 未就绪"}

        return instance.api_client.send_markdown_message(target_type, target_id, markdown)

    # ════════════════════════════════════════════════
    # 回调设置
    # ════════════════════════════════════════════════

    def set_message_callback(self, bot_name: str, callback: Callable):
        """设置消息处理回调

        Args:
            bot_name: Bot 名称
            callback: async def (qq_msg: QQMessage) -> str
        """
        self._callbacks[bot_name] = callback

        # 如果 Bot 已经在运行，更新 handler
        instance = self._instances.get(bot_name)
        if instance and instance.message_handler:
            instance.message_handler._on_user_message = callback

    # ════════════════════════════════════════════════
    # 状态查询
    # ════════════════════════════════════════════════

    def get_status(self, bot_name: str) -> dict:
        """获取 Bot 状态"""
        config = self.config_manager.get(bot_name)
        instance = self._instances.get(bot_name)

        if not config:
            return {"error": f"Bot '{bot_name}' 不存在"}

        status = {
            "name": config.name,
            "appId": config.appId[:4] + "****" if config.appId else "",
            "configured": config is not None,
            "enabled": config.enabled,
            "status": instance.status if instance else "stopped",
            "error": instance.error_message if instance else None,
            "started_at": instance.started_at if instance else None,
            "uptime": time.time() - instance.started_at if (instance and instance.started_at) else 0,
        }
        return status

    def get_all_status(self) -> dict:
        """获取所有 Bot 的状态"""
        statuses = {}
        for config in self.config_manager.list_all():
            statuses[config.name] = self.get_status(config.name)
        return statuses

    # ════════════════════════════════════════════════
    # 内部回调
    # ════════════════════════════════════════════════

    def _on_bot_ready(self, bot_name: str):
        """Bot 连接就绪"""
        instance = self._instances.get(bot_name)
        if instance:
            instance.status = "running"
            instance.error_message = None
            logger.info(f"🎉 Bot '{bot_name}' 已就绪，可以收发消息")

    def _on_bot_disconnect(self, bot_name: str, reason: str):
        """Bot 断开连接"""
        instance = self._instances.get(bot_name)
        if instance:
            instance.status = "error"
            instance.error_message = reason
            logger.warning(f"⚠ Bot '{bot_name}' 断开连接: {reason}")
            # gateway 内部已有自动重连逻辑，这里只更新状态
            # 如果重连彻底失败（reason 包含"失败"），尝试自动重启
            if "失败" in reason or "超限" in reason:
                logger.warning(f"🔄 Bot '{bot_name}' 重连失败，尝试自动重启...")
                # 在新线程中执行自动重启，避免阻塞回调
                def _auto_restart():
                    try:
                        time.sleep(5)
                        logger.info(f"🔄 自动重启 Bot '{bot_name}'...")
                        self.restart_bot(bot_name)
                    except Exception as e:
                        logger.error(f"自动重启失败: {e}")
                restart_thread = threading.Thread(target=_auto_restart, daemon=True)
                restart_thread.start()
