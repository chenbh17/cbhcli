"""QQ Bot 斜杠命令

提供 /qqbot 斜杠命令，用于管理 QQ Bot:
  /qqbot add       - 添加 QQ Bot 配置
  /qqbot list      - 列出已配置的 QQ Bot
  /qqbot start     - 启动 QQ Bot
  /qqbot stop      - 停止 QQ Bot
  /qqbot restart   - 重启 QQ Bot
  /qqbot status    - 查看 Bot 状态
  /qqbot rm        - 删除 Bot 配置
  /qqbot config    - 修改 Bot 配置

用法仿照 openclaw-qqbot 的命令风格。
"""
from cbhcli_pkg.commands.parser import SlashCommand
from cbhcli_pkg.qqbot.qqbot_config import QQBotConfig


def register_qqbot_commands(parser, app):
    """注册 QQ Bot 相关命令

    Args:
        parser: SlashCommandParser 实例
        app: CBHCLIApp 实例
    """

    def qqbot_handler(args):
        """QQ Bot 管理命令"""
        args = args.strip()
        if not args:
            return _qqbot_help()

        # 解析子命令
        parts = args.split(None, 1)
        subcmd = parts[0].lower()
        subargs = parts[1] if len(parts) > 1 else ""

        if subcmd == "add":
            return _qqbot_add(subargs, app)
        elif subcmd == "list":
            return _qqbot_list(app)
        elif subcmd == "start":
            return _qqbot_start(subargs, app)
        elif subcmd == "stop":
            return _qqbot_stop(subargs, app)
        elif subcmd == "restart":
            return _qqbot_restart(subargs, app)
        elif subcmd == "status":
            return _qqbot_status(subargs, app)
        elif subcmd == "rm":
            return _qqbot_rm(subargs, app)
        elif subcmd == "config":
            return _qqbot_config(subargs, app)
        elif subcmd == "help":
            return _qqbot_help()
        else:
            return f"❌ 未知子命令: {subcmd}\n\n{_qqbot_help()}"

    parser.register(SlashCommand(
        name="qqbot",
        description="管理 QQ Bot（添加/启动/停止/状态）",
        usage="<add|list|start|stop|restart|status|rm|config> [参数]",
        handler=qqbot_handler,
        requires_agent=False
    ))


def _qqbot_help() -> str:
    """显示 QQ Bot 帮助"""
    return """📋 QQ Bot 管理命令

用法: /qqbot <子命令> [参数]

子命令:
  add <名称> <AppID> <AppSecret> [BotToken]  添加 QQ Bot 配置（BotToken在开发设置页获取）
  list                               列出所有 QQ Bot
  start <名称>                       启动 QQ Bot
  stop <名称>                        停止 QQ Bot
  restart <名称>                     重启 QQ Bot
  status [名称]                      查看 Bot 状态（不指定则查看全部）
  rm <名称>                          删除 QQ Bot 配置
  config <名称> <key>=<value> ...    修改 Bot 配置

示例:
  /qqbot add mybot 12345678 abcdef123456
  /qqbot list
  /qqbot start mybot
  /qqbot status
  /qqbot stop mybot
  /qqbot rm mybot

前置步骤:
  1. 访问 https://q.qq.com/qqbot/openclaw/login.html
  2. 扫码登录并创建机器人
  3. 获取 AppID 和 AppSecret
  4. 使用 /qqbot add 添加到 cbhcli"""


def _qqbot_add(args: str, app) -> str:
    """添加 QQ Bot 配置

    用法: /qqbot add <名称> <AppID> <AppSecret> [agent]
    AppSecret 在 QQ 开放平台机器人「开发设置」页面获取。
    agent 可选，指定消息转发目标 Agent。
    """
    if not hasattr(app, 'qqbot_service') or not app.qqbot_service:
        return "❌ QQ Bot 服务未初始化"

    parts = args.strip().split()
    if len(parts) < 3:
        return "❌ 用法: /qqbot add <名称> <AppID> <AppSecret> [agent]"

    name = parts[0]
    app_id = parts[1]
    app_secret = parts[2]
    target_agent = parts[3] if len(parts) >= 4 else ""

    # 检查是否已存在
    existing = app.qqbot_service.config_manager.get(name)
    if existing:
        return f"❌ Bot '{name}' 已存在。如需修改请先 /qqbot rm {name}"

    # 验证 agent 是否存在
    if target_agent and not app.agent_manager.load_agent(target_agent):
        return f"❌ Agent '{target_agent}' 不存在，请先 /agent add {target_agent}"

    config = QQBotConfig(
        name=name,
        appId=app_id,
        appSecret=app_secret,
        intents=33555456,
        sandbox=True,
        enabled=True,
        target_agent=target_agent,
    )

    app.qqbot_service.config_manager.add(config)

    agent_info = f"\n  绑定 Agent: {target_agent}" if target_agent else ""

    return f"""✅ QQ Bot '{name}' 已添加

📋 配置信息:
  名称: {name}
  AppID: {app_id[:4]}****（已隐藏）
  启用: 是
  监听: C2C 私聊 + 群聊@消息{agent_info}

下一步: /qqbot start {name}"""

def _qqbot_list(app) -> str:
    """列出所有 QQ Bot 配置"""
    if not hasattr(app, 'qqbot_service') or not app.qqbot_service:
        return "❌ QQ Bot 服务未初始化"

    bots = app.qqbot_service.config_manager.list_all()

    if not bots:
        return """📭 没有配置任何 QQ Bot

添加 Bot:
  /qqbot add <名称> <AppID> <AppSecret>

获取凭证:
  访问 https://q.qq.com/qqbot/openclaw/login.html"""

    lines = ["📋 已配置的 QQ Bot:"]
    for bot in bots:
        status_info = app.qqbot_service.get_status(bot.name)
        status_icon = {"running": "🟢", "error": "🔴", "stopped": "⚪", "starting": "🟡"}.get(
            status_info.get("status", "stopped"), "⚪"
        )
        status_text = status_info.get("status", "stopped")
        enabled_text = "启用" if bot.enabled else "禁用"
        lines.append(
            f"  {status_icon} {bot.name}  [{enabled_text}]  ({status_text})"
        )

    return "\n".join(lines)


def _qqbot_start(args: str, app) -> str:
    """启动 QQ Bot"""
    if not hasattr(app, 'qqbot_service') or not app.qqbot_service:
        return "❌ QQ Bot 服务未初始化"

    name = args.strip()
    if not name:
        # 启动所有启用的 Bot
        app.qqbot_service.start_all_enabled()
        # 为所有 Bot 设置消息回调
        if hasattr(app, '_create_qqbot_callback'):
            for cfg in app.qqbot_service.config_manager.list_all():
                if cfg.enabled:
                    callback = app._create_qqbot_callback(cfg.name)
                    app.qqbot_service.set_message_callback(cfg.name, callback)
        return "✅ 正在启动所有已启用的 QQ Bot..."

    success = app.qqbot_service.start_bot(name)
    if success:
        # 自动设置消息回调：QQ 消息 → AI Agent 处理
        if hasattr(app, '_create_qqbot_callback'):
            callback = app._create_qqbot_callback(name)
            app.qqbot_service.set_message_callback(name, callback)

        # 获取目标 Agent 信息
        bot_config = app.qqbot_service.config_manager.get(name)
        target_agent = bot_config.target_agent if bot_config and bot_config.target_agent else app.current_agent_name
        agent_info = f" → Agent: {target_agent}" if target_agent else ""

        return f"✅ QQ Bot '{name}' 启动中...（等待连接就绪）{agent_info}"
    else:
        instance = app.qqbot_service._instances.get(name)
        if instance and instance.error_message:
            return f"❌ QQ Bot '{name}' 启动失败: {instance.error_message}"
        return f"❌ QQ Bot '{name}' 启动失败"


def _qqbot_stop(args: str, app) -> str:
    """停止 QQ Bot"""
    if not hasattr(app, 'qqbot_service') or not app.qqbot_service:
        return "❌ QQ Bot 服务未初始化"

    name = args.strip()
    if not name:
        return "❌ 用法: /qqbot stop <名称>"

    app.qqbot_service.stop_bot(name)
    return f"✅ QQ Bot '{name}' 已停止"


def _qqbot_restart(args: str, app) -> str:
    """重启 QQ Bot"""
    if not hasattr(app, 'qqbot_service') or not app.qqbot_service:
        return "❌ QQ Bot 服务未初始化"

    name = args.strip()
    if not name:
        return "❌ 用法: /qqbot restart <名称>"

    success = app.qqbot_service.restart_bot(name)
    if success:
        # 重新设置消息回调（restart 会创建新的 handler）
        if hasattr(app, '_create_qqbot_callback'):
            callback = app._create_qqbot_callback(name)
            app.qqbot_service.set_message_callback(name, callback)
        return f"✅ QQ Bot '{name}' 重启中..."
    else:
        return f"❌ QQ Bot '{name}' 重启失败"


def _qqbot_status(args: str, app) -> str:
    """查看 Bot 状态"""
    if not hasattr(app, 'qqbot_service') or not app.qqbot_service:
        return "❌ QQ Bot 服务未初始化"

    name = args.strip()

    if name:
        status = app.qqbot_service.get_status(name)
        if "error" in status:
            return f"❌ {status['error']}"

        status_icon = {"running": "🟢", "error": "🔴", "stopped": "⚪", "starting": "🟡"}.get(
            status["status"], "⚪"
        )

        lines = [
            f"📊 QQ Bot: {status['name']}",
            f"  状态:    {status_icon} {status['status']}",
            f"  AppID:   {status['appId']}",
            f"  启用:    {'是' if status['enabled'] else '否'}",
        ]
        # 显示绑定的 Agent
        bot_config = app.qqbot_service.config_manager.get(name)
        if bot_config:
            target = bot_config.target_agent or app.current_agent_name or "（未指定）"
            lines.append(f"  Agent:   {target}")
        if status.get("started_at"):
            import datetime
            uptime = datetime.timedelta(seconds=int(status["uptime"]))
            lines.append(f"  运行时间: {uptime}")
        if status.get("error"):
            lines.append(f"  错误:    {status['error']}")
        return "\n".join(lines)

    # 显示所有
    all_status = app.qqbot_service.get_all_status()
    if not all_status:
        return "📭 没有配置任何 QQ Bot"

    lines = ["📊 所有 QQ Bot 状态:"]
    for name, status in all_status.items():
        status_icon = {"running": "🟢", "error": "🔴", "stopped": "⚪", "starting": "🟡"}.get(
            status["status"], "⚪"
        )
        lines.append(f"  {status_icon} {name}: {status['status']}")
    return "\n".join(lines)


def _qqbot_rm(args: str, app) -> str:
    """删除 QQ Bot 配置"""
    if not hasattr(app, 'qqbot_service') or not app.qqbot_service:
        return "❌ QQ Bot 服务未初始化"

    name = args.strip()
    if not name:
        return "❌ 用法: /qqbot rm <名称>"

    # 先停止
    app.qqbot_service.stop_bot(name)

    # 删除配置
    result = app.qqbot_service.config_manager.remove(name)
    if result:
        return f"✅ QQ Bot '{name}' 已删除"
    else:
        return f"❌ Bot '{name}' 不存在"


def _qqbot_config(args: str, app) -> str:
    """修改 QQ Bot 配置

    用法: /qqbot config <名称> agent=myagent intents=1025 sandbox=false
    """
    if not hasattr(app, 'qqbot_service') or not app.qqbot_service:
        return "❌ QQ Bot 服务未初始化"

    parts = args.strip().split()
    if len(parts) < 2:
        return "❌ 用法: /qqbot config <名称> <key>=<value> ...\n\n可用配置项: agent, enabled, sandbox, intents"

    name = parts[0]
    config = app.qqbot_service.config_manager.get(name)
    if not config:
        return f"❌ Bot '{name}' 不存在"

    updates = {}
    for kv in parts[1:]:
        if '=' in kv:
            k, v = kv.split('=', 1)
            # 类型转换
            if v.lower() == 'true':
                v = True
            elif v.lower() == 'false':
                v = False
            elif v.isdigit() and k != 'target_agent' and k != 'agent':
                v = int(v)
            # agent 和 target_agent 都映射到 target_agent
            if k == 'agent':
                k = 'target_agent'
            updates[k] = v

    # 验证 agent 是否存在
    if 'target_agent' in updates:
        target = updates['target_agent']
        if target and not app.agent_manager.load_agent(target):
            return f"❌ Agent '{target}' 不存在，请先 /agent add {target}"

    # 应用更新
    for key, value in updates.items():
        if hasattr(config, key):
            setattr(config, key, value)
        else:
            return f"❌ 未知配置项: {key} (可用: agent, enabled, sandbox, intents)"

    # 保存
    app.qqbot_service.config_manager.add(config)

    return f"✅ QQ Bot '{name}' 配置已更新: {updates}"
