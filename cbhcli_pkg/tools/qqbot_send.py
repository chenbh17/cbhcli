"""QQ Bot 发消息工具

注册为 AI Agent 可调用的 Function Calling 工具，
让 AI 能够通过 QQ Bot 发送消息（文本/图片/文件）。

工具名称: qqbot_send_message

v5.3.0 增强（主动发送）:
- 支持直接指定 target_id (openid) 主动发消息（不需要用户先发消息）
- 支持按昵称/关键词查找用户 openid（find_user=true）
- 支持列出所有已知用户（action="list"）
- openid 持久化到 ~/.cbhcli/qqbot_registry.json，跨进程/跨 Agent 共享
- 发送走 REST API，不依赖 WebSocket 网关在线

QQ 官方消息规则提醒（工具描述中告知 AI）:
- 被动回复: 收到用户消息后 60 分钟内带 msg_id 回复，不限频次
- 主动消息: 每用户每月仅 4 条，且需用户开启"主动消息"开关；
  发送失败会返回官方错误说明
"""
import os
from cbhcli_pkg.tools.registry import BaseTool, ToolResult


class QQBotSendTool(BaseTool):
    """QQ Bot 发送消息工具

    允许 AI Agent 通过 QQ Bot 向指定用户或群发送文本、图片或文件。
    支持主动发送（不需要用户先发消息）。
    """

    def __init__(self, qqbot_service=None):
        """
        Args:
            qqbot_service: QQBotService 实例（由 CBHCLIApp 注入）
        """
        self._service = qqbot_service
        self._registry = None

    def set_service(self, service):
        """设置 QQBotService 实例"""
        self._service = service

    def _get_registry(self):
        """延迟导入用户注册表（避免无谓的文件 IO）"""
        if self._registry is None:
            from cbhcli_pkg.qqbot.qqbot_registry import QQBotUserRegistry
            self._registry = QQBotUserRegistry()
        return self._registry

    @property
    def name(self) -> str:
        return "qqbot_send_message"

    @property
    def description(self) -> str:
        return (
            "通过 QQ Bot 发送消息。可以向 QQ 私聊(c2c)或群聊(group)发送文本、图片或文件。"
            "发送图片/文件时，file_path 必须是本地文件的绝对路径。"
            "【重要】target_id 必须是 QQ 开放平台的 openid（不是 QQ 号），"
            "如果不知道目标 openid：用 action='list' 列出所有已知用户，"
            "或用 find_user='昵称关键词' 按昵称模糊查找。"
            "QQ 官方限制：主动消息每用户每月仅 4 条，发送失败时会返回官方错误说明。"
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
                    "description": (
                        "目标 openid。指定后直接向该 openid 发送（主动发送，无需用户先发消息）。"
                        "留空时自动使用最近活跃的私聊用户/群。"
                    )
                },
                "content": {
                    "type": "string",
                    "description": "要发送的消息内容"
                },
                "file_path": {
                    "type": "string",
                    "description": "要发送的本地文件绝对路径（可选，发送图片/文件时使用）"
                },
                "find_user": {
                    "type": "string",
                    "description": "按昵称关键词查找用户 openid（可选）。传入关键词返回匹配的用户列表，不发送消息"
                },
                "action": {
                    "type": "string",
                    "enum": ["list"],
                    "description": "action='list' 列出所有已知用户 openid（不发送消息）"
                },
                "bot_name": {
                    "type": "string",
                    "description": "指定用哪个 QQ Bot 发送（可选，留空自动选择第一个启用的 Bot）"
                }
            },
            "required": ["target_type", "content"]
        }

    def execute(self, target_type: str, content: str, target_id: str = "",
                file_path: str = "", find_user: str = "", action: str = "",
                bot_name: str = "") -> ToolResult:
        """执行发送消息

        Args:
            target_type: "c2c" 或 "group"
            content: 消息内容
            target_id: 目标 openid（留空自动获取最近活跃目标）
            file_path: 可选的文件路径（发送图片/文件时使用）
            find_user: 按昵称关键词查找用户（不发送）
            action: "list" 列出所有已知用户（不发送）
            bot_name: 指定 Bot 名称（留空自动选择）

        Returns:
            ToolResult
        """
        # ---- action: 列出所有已知用户 ----
        if action == "list":
            registry = self._get_registry()
            listing = registry.format_list()
            if listing.strip() and ("openid:" in listing):
                return ToolResult(success=True, output=listing)
            return ToolResult(
                success=False,
                output=listing,
                error="注册表中还没有任何用户记录。用户需要先在 QQ 上给 Bot 发过消息才会被记录。"
            )

        # ---- find_user: 按昵称查找 ----
        if find_user:
            registry = self._get_registry()
            matches = registry.find_by_name(find_user)
            if not matches:
                all_list = registry.format_list()
                return ToolResult(
                    success=False,
                    output=all_list,
                    error=f"没有找到昵称包含 '{find_user}' 的用户。以下是全部已知用户："
                )
            lines = [f"找到 {len(matches)} 个匹配 '{find_user}' 的用户："]
            for m in matches:
                lines.append(
                    f"  target_type: {m['message_type']} | openid: {m['openid']} | "
                    f"昵称: {m.get('name', '')} | Bot: {m.get('bot', '?')}"
                )
            lines.append("用 target_id=<openid> + target_type=<c2c/group> 发送消息。")
            return ToolResult(success=True, output="\n".join(lines))

        # ---- 参数校验 ----
        if target_type not in ("c2c", "group"):
            return ToolResult(success=False, output="",
                             error=f"target_type 必须是 'c2c' 或 'group'，当前: {target_type}")

        if not self._service:
            return ToolResult(
                success=False,
                output="",
                error="QQ Bot 服务未初始化，请先使用 /qqbot add 添加 Bot 并 /qqbot start 启动"
            )

        # ---- 获取可用的 Bot ----
        instance = self._pick_bot_instance(bot_name)
        if isinstance(instance, ToolResult):
            return instance  # 错误信息
        running_bot = instance[0]
        inst = instance[1]
        api = inst.api_client

        # ---- 确定目标 openid ----
        target_id = (target_id or "").strip()
        if not target_id:
            # 1) 先查持久化注册表（最近活跃）
            registry = self._get_registry()
            recent = registry.get_recent(target_type)
            if recent:
                target_id = recent["openid"]
            else:
                # 2) 再查当前进程内存上下文（兜底）
                handler = inst.message_handler
                if handler and handler._contexts:
                    for (author_id, msg_type), msgs in handler._contexts.items():
                        if msg_type == target_type and msgs:
                            if target_type == "c2c":
                                target_id = author_id
                            else:
                                last_msg = msgs[-1]
                                if getattr(last_msg, 'group_id', None):
                                    target_id = last_msg.group_id
                            if target_id:
                                break
                if not target_id:
                    return ToolResult(
                        success=False,
                        output=registry.format_list(),
                        error=(
                            "无法自动获取目标 ID：注册表为空（还没有用户给 Bot 发过消息）。"
                            "请让用户先在 QQ 上给 Bot 发一条消息，或直接指定 target_id (openid)。"
                            "可用 action='list' 查看所有已知用户。"
                        )
                    )

        # ---- 发送 ----
        # 如果指定了文件，先上传再发送
        if file_path:
            if not os.path.isfile(file_path):
                return ToolResult(success=False, output="",
                                  error=f"文件不存在: {file_path}")

            result = api.send_file_message(target_type, target_id, file_path)
            if 'error' in result:
                return ToolResult(success=False, output=str(result),
                                  error=self._friendly_error(result, target_type))
            # 如果还有文本内容，追发一条文本消息
            if content and content.strip():
                api.send_c2c_message(target_id, content) if target_type == 'c2c' \
                    else api.send_group_message(target_id, content)
            return ToolResult(success=True,
                              output=f"文件已通过 QQ Bot '{running_bot}' 发送到 {target_type}:{target_id}")

        # 纯文本消息
        result = self._service.send_message(running_bot, target_type, target_id, content)

        if 'error' in result:
            return ToolResult(
                success=False,
                output=str(result),
                error=self._friendly_error(result, target_type)
            )
        else:
            return ToolResult(
                success=True,
                output=f"消息已通过 QQ Bot '{running_bot}' 发送到 {target_type}:{target_id}"
            )

    # ────────────────────────────────────────────────
    # 内部方法
    # ────────────────────────────────────────────────

    def _pick_bot_instance(self, bot_name: str = ""):
        """选择一个可用的 Bot 实例

        Returns:
            (bot_name, BotInstance) 元组，或 ToolResult（失败时）
        """
        # 指定了 Bot 名称
        if bot_name:
            config = self._service.config_manager.get(bot_name)
            if not config:
                return ToolResult(success=False, output="",
                                  error=f"QQ Bot '{bot_name}' 不存在，可用 /qqbot list 查看")
            if not config.enabled:
                return ToolResult(success=False, output="",
                                  error=f"QQ Bot '{bot_name}' 已禁用，请先 /qqbot start {bot_name}")

            instance = self._service._instances.get(bot_name)
            # 未运行也无所谓：发送走 REST API，只要 api_client 能获取 token 即可
            if instance and instance.api_client:
                return (bot_name, instance)

            # 实例不存在（如本进程刚启动），创建一个纯 API 实例
            from cbhcli_pkg.qqbot.qqbot_service import BotInstance
            new_inst = BotInstance(config)
            self._service._instances[bot_name] = new_inst
            return (bot_name, new_inst)

        # 未指定：优先选已运行的（保证被动回复上下文可用），其次第一个启用的
        bots = self._service.config_manager.list_all()
        enabled_bots = [b for b in bots if b.enabled]
        if not enabled_bots:
            return ToolResult(success=False, output="",
                             error="没有已启用的 QQ Bot，请先 /qqbot add 添加")

        for bot in enabled_bots:
            status = self._service.get_status(bot.name)
            instance = self._service._instances.get(bot.name)
            if status.get("status") == "running" and instance and instance.api_client:
                return (bot.name, instance)

        # 没有运行中的 Bot：用第一个启用的 Bot 创建纯 API 实例
        # 发送走 REST API 不依赖网关，不需要 restart_bot
        # （避免多进程同时 start_bot 引发网关连接竞争）
        first = enabled_bots[0]
        instance = self._service._instances.get(first.name)
        if instance and instance.api_client:
            return (first.name, instance)
        from cbhcli_pkg.qqbot.qqbot_service import BotInstance
        new_inst = BotInstance(first)
        self._service._instances[first.name] = new_inst
        return (first.name, new_inst)

    @staticmethod
    def _friendly_error(result: dict, target_type: str) -> str:
        """把 QQ 官方错误码翻译成对 AI 友好的提示"""
        detail = str(result.get('detail', result.get('error', '')))
        base = f"发送失败: {result}"

        if "40034102" in detail or "主动消息失败" in detail or "无权限" in detail:
            return (
                base
                + "\n【QQ 官方限制】机器人主动推送消息需要用户在 Bot 资料卡开启"
                "'主动消息'推送开关，且每用户每月仅能收到 4 条主动消息。"
                "建议请用户先在 QQ 上给 Bot 发一条消息，之后 60 分钟内的回复不受限制。"
            )
        if "无好友关系" in detail or "11245" in detail or "11248" in detail:
            return (
                base
                + "\n【QQ 官方限制】目标用户尚未添加机器人为好友。"
                "用户需在 QQ 中打开机器人资料卡点击'发消息'。"
            )
        if "主动消息超过频率限制" in detail or "频" in detail:
            return (
                base
                + "\n【QQ 官方限制】主动消息频率超限（每用户每月 4 条/每群每月 4 条）。"
                "请让用户先发一条消息，之后被动回复不受限制。"
            )
        if target_type == "group" and "群" in detail:
            return (
                base
                + "\n提示: 群聊 target_id 必须是 group_openid（不是群号）。"
                "可用 action='list' 查看已知的群 openid。"
            )
        return base