"""QQ Bot 发消息工具

注册为 AI Agent 可调用的 Function Calling 工具，
让 AI 能够通过 QQ Bot 发送消息（文本/图片/文件）。

工具名称: qqbot_send_message
"""
from cbhcli_pkg.tools.registry import BaseTool, ToolResult


class QQBotSendTool(BaseTool):
    """QQ Bot 发送消息工具

    允许 AI Agent 通过 QQ Bot 向指定用户或群发送文本、图片或文件。
    """

    def __init__(self, qqbot_service=None):
        """
        Args:
            qqbot_service: QQBotService 实例（由 CBHCLIApp 注入）
        """
        self._service = qqbot_service

    def set_service(self, service):
        """设置 QQBotService 实例"""
        self._service = service

    @property
    def name(self) -> str:
        return "qqbot_send_message"

    @property
    def description(self) -> str:
        return (
            "通过 QQ Bot 发送消息。可以向 QQ 私聊或群聊发送文本消息、图片或文件。"
            "发送图片/文件时，file_path 必须是本地文件的绝对路径。"
        )
    
    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "target_type": {
                    "type": "string",
                    "description": "目标类型: 'c2c' (私聊) 或 'group' (群聊)",
                    "enum": ["c2c", "group"]
                },
                "target_id": {
                    "type": "string",
                    "description": "目标 ID。私聊时可留空，自动使用最近和你 QQ 对话的用户 openid"
                },
                "content": {
                    "type": "string",
                    "description": "要发送的消息内容"
                },
                "file_path": {
                    "type": "string",
                    "description": "要发送的本地文件绝对路径（可选）"
                }
            },
            "required": ["target_type", "content"]
        }

    def execute(self, target_type: str, content: str, target_id: str = "",
                file_path: str = "") -> ToolResult:
        """执行发送消息

        Args:
            target_type: "c2c" 或 "group"
            target_id: 目标 openid
            content: 消息内容
            file_path: 可选的文件路径（发送图片/文件时使用）

        Returns:
            ToolResult
        """
        if not self._service:
            return ToolResult(
                success=False,
                output="",
                error="QQ Bot 服务未初始化，请先使用 /qqbot add 添加 Bot 并 /qqbot start 启动"
            )

        # 获取第一个可用的 Bot，如果都断了就自动重启
        bots = self._service.config_manager.list_all()
        enabled_bots = [b for b in bots if b.enabled]
        if not enabled_bots:
            return ToolResult(success=False, output="",
                error="没有已启用的 QQ Bot，请先 /qqbot add 添加")

        running_bot = None
        for bot in enabled_bots:
            status = self._service.get_status(bot.name)
            if status.get("status") == "running":
                running_bot = bot.name
                break

        if not running_bot:
            # 自动重启第一个已启用的 Bot
            first_bot = enabled_bots[0].name
            ok = self._service.restart_bot(first_bot)
            if not ok:
                return ToolResult(success=False, output="",
                    error=f"QQ Bot '{first_bot}' 无法启动，请手动 /qqbot start {first_bot}")
            # 重新设置回调
            import time
            time.sleep(1)
            running_bot = first_bot

        instance = self._service._instances.get(running_bot)
        if not instance or not instance.api_client:
            return ToolResult(
                success=False, output="",
                error=f"Bot '{running_bot}' API 客户端未就绪"
            )

        api = instance.api_client

        # 如果没传 target_id，从最近 QQ 消息上下文自动获取
        if not target_id:
            handler = instance.message_handler
            if handler and handler._contexts:
                if target_type == "c2c":
                    # 取最近的一个私聊 openid
                    for (author_id, msg_type), msgs in handler._contexts.items():
                        if msg_type == "c2c" and msgs:
                            target_id = author_id
                            break
                elif target_type == "group":
                    # 取最近的一个群聊 group_openid
                    for (author_id, msg_type), msgs in handler._contexts.items():
                        if msg_type == "group" and msgs:
                            # 从最后一条消息中取 group_id
                            last_msg = msgs[-1]
                            if hasattr(last_msg, 'group_id') and last_msg.group_id:
                                target_id = last_msg.group_id
                                break
            if not target_id:
                hint = "私聊请先在 QQ 上给 Bot 发一条消息" if target_type == "c2c" \
                    else "群聊请先在群里 @机器人 发一条消息"
                return ToolResult(success=False, output="",
                    error=f"无法自动获取目标 ID，{hint}，或直接指定 target_id")

        # 如果指定了文件，先上传再发送
        if file_path:
            import os
            if not os.path.isfile(file_path):
                return ToolResult(success=False, output="",
                                  error=f"文件不存在: {file_path}")

            result = api.send_file_message(target_type, target_id, file_path)
            if 'error' in result:
                return ToolResult(success=False, output=str(result),
                                  error=result.get('error', '发送失败'))
            # 如果还有文本内容，追发一条文本消息
            if content and content.strip():
                api.send_c2c_message(target_id, content) if target_type == 'c2c' \
                    else api.send_group_message(target_id, content)
            return ToolResult(success=True,
                              output=f"文件已通过 QQ Bot '{running_bot}' 发送")

        # 纯文本消息
        result = self._service.send_message(running_bot, target_type, target_id, content)

        if 'error' in result:
            return ToolResult(
                success=False,
                output=str(result),
                error=result.get('error', '未知错误')
            )
        else:
            return ToolResult(
                success=True,
                output=f"消息已通过 QQ Bot '{running_bot}' 发送"
            )
