"""Agent命令处理"""
from cbhcli_pkg.commands.parser import SlashCommand


def register_agent_commands(parser, app):
    """注册Agent相关命令"""

    # /agent 命令
    def agent_handler(args):
        args = args.strip()

        if not args:
            # 无参数时显示交互式选择菜单
            return _show_agent_menu(app)

        parts = args.split(None, 1)
        action = parts[0].lower()
        param = parts[1] if len(parts) > 1 else ""

        if action == "add":
            if not param:
                return "❌ 用法: /agent add <name>"
            return _create_agent(app, param.strip())

        elif action == "list":
            return _list_agents(app)

        elif action == "use":
            if not param:
                # 无参数时显示交互式选择菜单
                return _show_agent_menu(app)
            return _switch_agent(app, param.strip())

        elif action == "rm":
            if not param:
                return _show_rm_menu(app)
            return _delete_agent(app, param.strip())

        else:
            return f"❌ 未知操作: {action}"

    parser.register(SlashCommand(
        name="agent",
        description="管理Agent",
        usage="add|list|use|rm [name]",
        handler=agent_handler
    ))


def _show_agent_menu(app):
    """显示Agent交互式选择菜单"""
    agents = app.agent_manager.list_agents()

    if not agents:
        return "📭 暂无Agent。使用 /agent add <name> 创建第一个Agent。"

    lines = ["📋 选择Agent (输入编号或名称):\n"]
    active = app.current_agent_name

    for i, agent in enumerate(agents, 1):
        marker = " ◀ 当前" if agent.name == active else ""
        lines.append(f"  {i}. {agent.name}{marker}")
        if agent.description:
            lines.append(f"     {agent.description}")
        if agent.primary_model:
            lines.append(f"     模型: {agent.primary_model}")
        lines.append("")

    lines.append(f"  0. 取消")
    lines.append("")

    # 显示菜单
    print("\n" + "\n".join(lines))

    # 获取用户选择
    choice = input("请选择 [编号/名称]: ").strip()

    if not choice or choice == '0':
        return "已取消选择"

    # 尝试按编号解析
    if choice.isdigit():
        idx = int(choice)
        if idx == 0:
            return "已取消选择"
        if 1 <= idx <= len(agents):
            return _switch_agent(app, agents[idx - 1].name)
        else:
            return f"❌ 无效编号 (1-{len(agents)})"

    # 尝试按名称匹配
    for agent in agents:
        if agent.name.lower() == choice.lower():
            return _switch_agent(app, agent.name)

    return f"❌ 未找到Agent: {choice}"


def _create_agent(app, name):
    """创建新Agent"""
    # 检查是否已存在
    if app.agent_manager.load_agent(name):
        return f"❌ Agent '{name}' 已存在"

    description = input("请输入Agent描述 (可选): ").strip()

    # 获取可用模型
    models = app.global_config.get_models()
    primary_model = None
    if models:
        print("\n可用模型:")
        for i, model in enumerate(models, 1):
            ctx = model.get('context_limit', 128000)
            print(f"  {i}. {model['name']} ({model['model']}) - 上下文: {ctx:,}")

        model_choice = input("\n请选择首选模型编号 (直接回车跳过): ").strip()
        if model_choice.isdigit():
            idx = int(model_choice) - 1
            if 0 <= idx < len(models):
                primary_model = models[idx]['name']

    app.agent_manager.create_agent(name, description, primary_model)

    # 加载Agent（包括模型和会话）
    if app._load_agent(name):
        app.global_config.set_active_agent(name)
        return f"✅ Agent '{name}' 创建成功并已激活!"
    else:
        return f"✅ Agent '{name}' 创建成功，但模型未配置"


def _list_agents(app):
    """列出所有Agent"""
    agents = app.agent_manager.list_agents()

    if not agents:
        return "📭 暂无Agent。使用 /agent add <name> 创建第一个Agent。"

    active = app.current_agent_name
    lines = ["📋 已配置的Agent:\n"]

    for agent in agents:
        marker = " ◀ 当前" if agent.name == active else ""
        lines.append(f"  • {agent.name}{marker}")
        if agent.description:
            lines.append(f"    {agent.description}")
        if agent.primary_model:
            lines.append(f"    模型: {agent.primary_model}")
        lines.append("")

    return "\n".join(lines)


def _switch_agent(app, name):
    """切换到指定Agent"""
    # 加载Agent（包括模型和会话）
    if app._load_agent(name):
        app.global_config.set_active_agent(name)
        return f"✅ 已切换到Agent: {name}"
    else:
        return f"❌ Agent '{name}' 不存在或加载失败"


def _show_rm_menu(app):
    """显示Agent删除选择菜单"""
    agents = app.agent_manager.list_agents()
    active = app.current_agent_name

    # 过滤掉 main 和当前 agent
    deletable = [a for a in agents if a.name != "main" and a.name != active]
    if not deletable:
        return "📭 没有可删除的Agent（不能删除 'main' 和当前激活的Agent）"

    lines = ["📋 选择要删除的Agent (输入编号或名称):\n"]
    for i, agent in enumerate(deletable, 1):
        lines.append(f"  {i}. {agent.name}")
        if agent.description:
            lines.append(f"     {agent.description}")
        lines.append("")

    lines.append(f"  0. 取消")
    lines.append("")

    print("\n" + "\n".join(lines))
    choice = input("请选择 [编号/名称]: ").strip()

    if not choice or choice == '0':
        return "已取消"

    if choice.isdigit():
        idx = int(choice)
        if idx == 0:
            return "已取消"
        if 1 <= idx <= len(deletable):
            return _delete_agent(app, deletable[idx - 1].name)
        else:
            return f"❌ 无效编号 (1-{len(deletable)})"

    for agent in deletable:
        if agent.name.lower() == choice.lower():
            return _delete_agent(app, agent.name)

    return f"❌ 未找到Agent: {choice}"


def _delete_agent(app, name):
    """删除Agent"""
    # 'main' Agent 不能删除
    if name == "main":
        return "❌ 无法删除主默认Agent 'main'"

    if name == app.current_agent_name:
        return "❌ 无法删除当前激活的Agent"

    confirm = input(f"确定要删除Agent '{name}' 吗? (y/n): ").strip().lower()
    if confirm != 'y':
        return "已取消删除"

    if app.agent_manager.delete_agent(name):
        return f"✅ Agent '{name}' 已删除"
    else:
        return f"❌ Agent '{name}' 不存在"
