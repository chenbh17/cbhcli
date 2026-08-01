"""Agent 链条命令: /chain

所有子命令均支持无参数直接进入交互式引导：
  list    - 列出所有链条（树形展示）
  add     - 交互式创建链条（引导输入名称 → 逐层选择 Agent）
  rm      - 删除链条（从列表选择）
  use     - 激活链条（从列表选择）
  off     - 取消链条绑定
  show    - 查看链条详情（从列表选择）
  config  - 编辑链条配置（从列表选择）
  rename  - 重命名链条（从列表选择）
"""
from cbhcli_pkg.commands.parser import SlashCommand
from cbhcli_pkg.core.prompt_utils import ask_text, ask_text_or_none


def register_chain_commands(parser, app):
    """注册 /chain 命令"""

    def chain_handler(args):
        args = args.strip()
        if not args:
            return (
                "🔗 Agent 链条命令:\n"
                "  /chain list    - 列出所有链条\n"
                "  /chain add     - 创建新链条（交互式引导）\n"
                "  /chain rm      - 删除链条\n"
                "  /chain use     - 激活链条\n"
                "  /chain off     - 取消链条绑定\n"
                "  /chain show    - 查看链条详情\n"
                "  /chain config  - 编辑链条配置\n"
                "  /chain rename  - 重命名链条\n"
            )

        parts = args.split(None, 1)
        action = parts[0].lower()
        rest = parts[1].strip() if len(parts) > 1 else ""

        if action == "list":
            return _cmd_list(app)
        elif action == "add":
            return _cmd_add(app, rest)
        elif action == "rm":
            return _cmd_rm(app, rest)
        elif action == "use":
            return _cmd_use(app, rest)
        elif action == "off":
            return _cmd_off(app)
        elif action == "show":
            return _cmd_show(app, rest)
        elif action == "config":
            return _cmd_config(app, rest)
        elif action == "rename":
            return _cmd_rename(app, rest)
        else:
            return f"❌ 未知子命令: {action}\n可用: list, add, rm, use, off, show, config, rename"

    parser.register(SlashCommand(
        name="chain",
        description="Agent 链条管理（多 Agent 调用编排）",
        usage="list|add|rm|use|off|show|config|rename",
        handler=chain_handler,
        requires_agent=True
    ))


def _get_chain_manager(app):
    """获取或创建 ChainManager"""
    if not getattr(app, '_chain_manager', None):
        from cbhcli_pkg.core.agent_chain import ChainManager
        app._chain_manager = ChainManager()
    return app._chain_manager


# ──────────────────────────────────────────────
#  通用交互辅助
# ──────────────────────────────────────────────

def _select_chain(app, prompt_text="选择链条") -> "str | None":
    """交互式从已有链条列表中选择一个，返回链条名称或 None（取消）"""
    cm = _get_chain_manager(app)
    chains = cm.list_chains()
    if not chains:
        print("🔗 暂无已配置的链条，请先使用 /chain add 创建")
        return None

    print(f"\n{prompt_text}:")
    for i, c in enumerate(chains, 1):
        desc = f" - {c.description}" if c.description else ""
        current = " (已激活)" if getattr(app, '_active_chain', None) and app._active_chain.name == c.name else ""
        print(f"  {i}. {c.name}{desc}{current}")
    print(f"  0. 取消")

    choice = ask_text("\n请选择编号: ").strip()
    if not choice or choice == "0":
        return None
    try:
        idx = int(choice)
    except ValueError:
        print(f"❌ 无效编号: {choice}")
        return None
    if idx < 1 or idx > len(chains):
        print(f"❌ 编号超出范围 (1-{len(chains)})")
        return None
    return chains[idx - 1].name


def _select_agents(available: list, prompt_text="选择 Agent",
                   multi: bool = False) -> "list[str] | str | None":
    """交互式选择 Agent（编号选择，非手动输入名称）

    Args:
        available: 可选 Agent 名称列表
        prompt_text: 提示文本
        multi: 是否多选

    Returns:
        multi=True -> list[str] 或 None（取消）
        multi=False -> str 或 None（取消）
    """
    if not available:
        print("❌ 没有可选 Agent")
        return None

    print(f"\n{prompt_text}:")
    for i, name in enumerate(available, 1):
        print(f"  {i}. {name}")
    hint = "（可多选，逗号分隔，支持范围如 1-3）" if multi else ""
    print(f"  0. 取消")
    choice = ask_text(f"\n请选择编号{hint}: ").strip()

    if not choice or choice == "0":
        return None

    # 解析编号
    indices = _parse_multi_choice(choice, len(available))
    if indices is None:
        print("❌ 无效输入")
        return None
    if not indices:
        print("❌ 未选择任何 Agent")
        return None

    selected = [available[i] for i in indices]
    if multi:
        return selected
    else:
        if len(selected) > 1:
            print("⚠️  单选模式只取第一个")
        return selected[0]


def _parse_multi_choice(choice: str, max_idx: int) -> "list[int] | None":
    """解析多选输入（逗号分隔的编号，支持范围如 1-5）

    Returns:
        选中的索引列表（0-based），无效输入返回 None
    """
    indices = []
    parts = [p.strip() for p in choice.split(",")]
    for part in parts:
        if not part:
            continue
        if '-' in part:
            try:
                start, end = part.split('-', 1)
                start, end = int(start.strip()), int(end.strip())
            except (ValueError, TypeError):
                return None
            if start < 1 or end > max_idx or start > end:
                return None
            for i in range(start, end + 1):
                if (i - 1) not in indices:
                    indices.append(i - 1)
        else:
            if not part.isdigit():
                return None
            idx = int(part)
            if idx < 1 or idx > max_idx:
                return None
            if (idx - 1) not in indices:
                indices.append(idx - 1)
    return indices


# ──────────────────────────────────────────────
#  子命令实现
# ──────────────────────────────────────────────

def _cmd_list(app) -> str:
    cm = _get_chain_manager(app)
    chains = cm.list_chains()

    if not chains:
        return (
            "🔗 暂无已配置的 Agent 链条\n\n"
            "使用 /chain add 创建新链条"
        )

    from cbhcli_pkg.core.agent_chain import render_chain_tree

    lines = ["🔗 Agent 链条列表\n"]
    for chain in chains:
        lines.append(f"[{chain.name}]")
        if chain.description:
            lines.append(f"  描述: {chain.description}")
        tree = render_chain_tree(chain, app.agent_manager, show_details=False)
        for line in tree.split('\n'):
            lines.append(f"  {line}")
        lines.append("")

    current = getattr(app, '_active_chain', None)
    if current:
        lines.append(f"当前会话: 🔗 {current.name} (已激活)")
    else:
        lines.append("当前会话: 未绑定链条")

    return "\n".join(lines)


def _cmd_add(app, name: str = "") -> str:
    cm = _get_chain_manager(app)

    # 名称：无参数时交互式输入
    if not name:
        print("\n🔗 创建新 Agent 链条\n")
        while True:
            name = ask_text("链条名称: ").strip()
            if not name:
                return "已取消"
            if cm.get_chain(name):
                print(f"❌ 链条 '{name}' 已存在，请换一个\n")
                continue
            break
    else:
        if cm.get_chain(name):
            return f"❌ 链条 '{name}' 已存在"

    # 获取所有可用 Agent
    agents = app.agent_manager.list_agents()
    if not agents:
        return "❌ 没有可用的 Agent，请先使用 /agent add 创建"

    agent_names = [a.name for a in agents]

    print(f"\n🔗 创建 Agent 链条: {name}")
    print(f"可用 Agent: {' / '.join(agent_names)}\n")

    description = ask_text_or_none("链条描述 (可选，直接回车跳过): ")
    # ask_text_or_none 回车返回空字符串""，不是 None
    description = description if description else ""

    from cbhcli_pkg.core.agent_chain import AgentChain, ChainLevel, ChainAgent

    chain = AgentChain(name=name, description=description)

    # Level 1: 元 Agent（单选）
    print(f"\n── Level 1 (元 Agent) ──")
    root_choice = _select_agents(agent_names, "选择元 Agent", multi=False)
    if root_choice is None:
        return "已取消"

    chain.levels.append(ChainLevel(
        level=1,
        agents=[ChainAgent(name=root_choice)]
    ))

    # 后续层级
    level_num = 1
    while True:
        level_num += 1
        print(f"\n── Level {level_num} ──")
        used = chain.get_all_agent_names()
        available = [n for n in agent_names if n not in used]
        if not available:
            print("所有 Agent 已使用完毕")
            break

        selected = _select_agents(available, f"选择 Level {level_num} Agent", multi=True)
        if selected is None:
            print("跳过本层")
            level_num -= 1
            break

        level_agents = []
        for sel in selected:
            instr = ask_text_or_none(f"调用说明 - {sel} (可选，直接回车跳过): ")
            instr = instr if instr else ""
            level_agents.append(ChainAgent(name=sel, call_instruction=instr))

        chain.levels.append(ChainLevel(level=level_num, agents=level_agents))

        cont = ask_text("继续添加下一层? (y/n): ").strip().lower()
        if cont not in ('y', 'yes'):
            break

    # 确认
    from cbhcli_pkg.core.agent_chain import render_chain_tree
    print("\n确认链条结构:")
    print(render_chain_tree(chain, app.agent_manager, show_details=False))

    confirm = ask_text("\n保存? (y/n): ").strip().lower()
    if confirm not in ('y', 'yes'):
        return "已取消"

    # 校验 Agent 存在性
    missing = chain.validate(app.agent_manager)
    if missing:
        return f"❌ 链条中引用了不存在的 Agent: {', '.join(missing)}"

    cm.add_chain(chain)
    return f"✅ 链条 '{name}' 已保存"


def _cmd_rm(app, name: str = "") -> str:
    cm = _get_chain_manager(app)

    if not name:
        name = _select_chain(app, "选择要删除的链条")
        if name is None:
            return "已取消"

    if not cm.get_chain(name):
        return f"❌ 链条 '{name}' 不存在"

    # 如果是当前激活的链条，取消绑定
    current = getattr(app, '_active_chain', None)
    if current and current.name == name:
        _deactivate_chain(app)

    cm.remove_chain(name)
    return f"✅ 链条 '{name}' 已删除"


def _cmd_use(app, name: str = "") -> str:
    cm = _get_chain_manager(app)

    if not name:
        name = _select_chain(app, "选择要激活的链条")
        if name is None:
            return "已取消"

    chain = cm.get_chain(name)
    if not chain:
        return f"❌ 链条 '{name}' 不存在"

    # 校验 Agent 存在性
    missing = chain.validate(app.agent_manager)
    if missing:
        return (
            f"❌ 链条中引用了不存在的 Agent: {', '.join(missing)}\n"
            f"请使用 /chain config 修复或 /chain rm 删除"
        )

    # 检查当前 Agent 是否为链条的元 Agent
    root = chain.get_root_agent()
    current_agent = app.current_agent_name or ""

    if current_agent != root:
        return (
            f"⚠️  链条 '{name}' 的元 Agent 是 '{root}'，"
            f"当前 Agent 是 '{current_agent}'。\n"
            f"请先使用 /agent use {root} 切换到元 Agent，再激活链条。"
        )

    # 如果已有激活链条，提示切换
    current = getattr(app, '_active_chain', None)
    if current and current.name != name:
        confirm = ask_text_or_none(
            f"当前已激活链条 '{current.name}'，是否切换到 '{name}'? [y/N]: "
        )
        if not confirm or confirm.strip().lower() not in ('y', 'yes'):
            return "已取消"

    _activate_chain(app, chain)
    return (
        f"✅ 链条 '{name}' 已激活\n"
        f"   元 Agent: {root}\n"
        f"   状态栏将显示: 🔗 {name} › {root}"
    )


def _cmd_off(app) -> str:
    current = getattr(app, '_active_chain', None)
    if not current:
        return "当前未绑定链条"

    _deactivate_chain(app)
    return "✅ 已取消链条绑定，恢复为普通单 Agent 模式"


def _cmd_show(app, name: str = "") -> str:
    cm = _get_chain_manager(app)

    if not name:
        name = _select_chain(app, "选择要查看的链条")
        if name is None:
            return "已取消"

    chain = cm.get_chain(name)
    if not chain:
        return f"❌ 链条 '{name}' 不存在"

    from cbhcli_pkg.core.agent_chain import render_chain_tree

    lines = [f"🔗 链条名称: {name}\n"]
    if chain.description:
        lines.append(f"描述: {chain.description}")
    lines.append("")

    # 校验 Agent 存在性
    missing = chain.validate(app.agent_manager)
    if missing:
        lines.append(f"⚠️  链条中引用了不存在的 Agent: {', '.join(missing)}")
        lines.append("")

    tree = render_chain_tree(chain, app.agent_manager, show_details=True)
    lines.append(tree)

    current = getattr(app, '_active_chain', None)
    if current and current.name == name:
        lines.append(f"\n当前会话: 🔗 {name} (已激活)")

    return "\n".join(lines)


def _cmd_config(app, name: str = "") -> str:
    cm = _get_chain_manager(app)

    if not name:
        name = _select_chain(app, "选择要编辑的链条")
        if name is None:
            return "已取消"

    chain = cm.get_chain(name)
    if not chain:
        return f"❌ 链条 '{name}' 不存在"

    from cbhcli_pkg.core.agent_chain import render_chain_tree, ChainLevel, ChainAgent

    while True:
        print(f"\n🔗 编辑链条: {name}\n")
        print("当前结构:")
        print(render_chain_tree(chain, app.agent_manager, show_details=False))
        print()
        if chain.description:
            print(f"描述: {chain.description}")
        else:
            print("描述: (无)")
        print()

        print("编辑选项:")
        print("  1. 修改描述")
        print("  2. 修改调用说明")
        print("  3. 添加层级")
        print("  4. 删除最后层级")
        print("  5. 替换某层 Agent")
        print("  0. 完成")

        choice = ask_text("\n选择操作: ").strip()

        if choice == "0" or not choice:
            return "✅ 已退出编辑"

        elif choice == "1":
            current_desc = chain.description or "(无)"
            new_desc = ask_text_or_none(
                f"新描述 (当前: {current_desc}, 直接回车跳过, 'clear' 清空): ")
            # ask_text_or_none: 回车返回""，Ctrl+C/Esc 返回 None
            if new_desc is None:
                print("已跳过")
            elif new_desc == "":
                print("已跳过（空输入）")
            elif new_desc.strip().lower() == 'clear':
                chain.description = ""
                cm.update_chain(name, chain)
                print("✅ 描述已清空")
            else:
                chain.description = new_desc.strip()
                cm.update_chain(name, chain)
                print("✅ 描述已更新")

        elif choice == "2":
            # 列出所有非元 Agent 的调用说明，逐个修改
            has_agent = False
            for level in chain.levels:
                for agent in level.agents:
                    if level.level == 1:
                        continue
                    has_agent = True
                    current_instr = agent.call_instruction or "(无)"
                    print(f"\n  {agent.name} (Level {level.level})")
                    print(f"  当前调用说明: {current_instr}")
                    new_instr = ask_text_or_none(
                        "  新调用说明 (直接回车跳过, 'clear' 清空): ")
                    if new_instr is None:
                        print("  已跳过")
                    elif new_instr == "":
                        print("  已跳过（空输入）")
                    elif new_instr.strip().lower() == 'clear':
                        agent.call_instruction = ""
                        print("  ✅ 已清空")
                    else:
                        agent.call_instruction = new_instr.strip()
                        print("  ✅ 已更新")
            if not has_agent:
                print("\n链条中没有可修改调用说明的 Agent（仅元 Agent）")
            cm.update_chain(name, chain)

        elif choice == "3":
            # 添加新层级
            next_level = len(chain.levels) + 1
            agents = app.agent_manager.list_agents()
            used = chain.get_all_agent_names()
            available = [a.name for a in agents if a.name not in used]

            if not available:
                print("\n❌ 所有 Agent 已使用完毕")
                continue

            print(f"\n── 添加 Level {next_level} ──")
            selected = _select_agents(available, f"选择 Level {next_level} Agent", multi=True)
            if not selected:
                print("已取消")
                continue

            level_agents = []
            for sel in selected:
                instr = ask_text_or_none(f"调用说明 - {sel} (可选，直接回车跳过): ")
                instr = instr if instr else ""
                level_agents.append(ChainAgent(name=sel, call_instruction=instr))

            if level_agents:
                chain.levels.append(ChainLevel(level=next_level, agents=level_agents))
                cm.update_chain(name, chain)
                print(f"✅ 已添加 Level {next_level}")

        elif choice == "4":
            if len(chain.levels) <= 1:
                print("\n❌ 无法删除元 Agent 层级")
                continue
            removed = chain.levels.pop()
            cm.update_chain(name, chain)
            agents_str = ", ".join(a.name for a in removed.agents)
            print(f"✅ 已删除 Level {removed.level} ({agents_str})")

        elif choice == "5":
            # 替换某层 Agent
            print()
            for i, level in enumerate(chain.levels):
                agents_str = ", ".join(a.name for a in level.agents)
                print(f"  Level {level.level}: {agents_str}")

            level_input = ask_text("\n选择层级编号 (0 取消): ").strip()
            if not level_input or level_input == "0":
                continue
            try:
                level_num = int(level_input)
            except ValueError:
                print("❌ 无效层级编号")
                continue

            target_level = chain.get_level(level_num)
            if not target_level:
                print(f"❌ 层级 {level_num} 不存在")
                continue

            agents = app.agent_manager.list_agents()
            current_names = [a.name for a in target_level.agents]
            # 可选 = 未被其他层使用的 + 本层已有的
            available = [a.name for a in agents
                         if a.name not in chain.get_all_agent_names() or a.name in current_names]

            selected = _select_agents(available, f"选择 Level {level_num} 新 Agent", multi=True)
            if not selected:
                print("已取消")
                continue

            new_agents = []
            for sel in selected:
                # 保留原有调用说明
                instr = ""
                for a in target_level.agents:
                    if a.name == sel:
                        instr = a.call_instruction
                        break
                new_instr = ask_text_or_none(
                    f"调用说明 - {sel} (当前: {instr or '无'}, 直接回车保持, 'clear' 清空): ")
                if new_instr is None or new_instr == "":
                    pass  # 保持原有
                elif new_instr.strip().lower() == 'clear':
                    instr = ""
                else:
                    instr = new_instr.strip()
                new_agents.append(ChainAgent(name=sel, call_instruction=instr))

            if new_agents:
                target_level.agents = new_agents
                cm.update_chain(name, chain)
                print(f"✅ Level {level_num} 已更新")

        else:
            print("❌ 无效选项")

        # 询问是否继续
        cont = ask_text("\n继续编辑? (y/n): ").strip().lower()
        if cont not in ('y', 'yes'):
            return "✅ 已退出编辑"


def _cmd_rename(app, args: str = "") -> str:
    cm = _get_chain_manager(app)
    parts = args.split() if args else []

    old_name = parts[0] if len(parts) >= 1 else ""
    new_name = parts[1] if len(parts) >= 2 else ""

    if not old_name:
        old_name = _select_chain(app, "选择要重命名的链条")
        if old_name is None:
            return "已取消"

    if not cm.get_chain(old_name):
        return f"❌ 链条 '{old_name}' 不存在"

    if not new_name:
        new_name = ask_text("新名称: ").strip()
        if not new_name:
            return "已取消"

    if cm.get_chain(new_name):
        return f"❌ 链条 '{new_name}' 已存在"

    cm.rename_chain(old_name, new_name)

    # 更新当前激活状态
    current = getattr(app, '_active_chain', None)
    if current and current.name == old_name:
        current.name = new_name

    return f"✅ 链条已重命名: {old_name} -> {new_name}"


# ──────────────────────────────────────────────
#  内部辅助
# ──────────────────────────────────────────────

def _activate_chain(app, chain):
    """激活链条"""
    app._active_chain = chain
    app._chain_active_path = [chain.get_root_agent()]

    # 持久化激活状态（重新进入 Agent 时自动恢复）
    root = chain.get_root_agent()
    if root:
        app.global_config.set_active_chain(root, chain.name)

    # 注入链条信息到系统提示
    _inject_chain_prompt(app, chain)

    # 注册 call_agent 工具（如果当前 Agent 有下游）
    _register_call_agent_tool(app, chain)


def _deactivate_chain(app):
    """取消链条绑定"""
    app._active_chain = None
    app._chain_active_path = None

    # 持久化取消状态
    if app.current_agent_name:
        app.global_config.set_active_chain(app.current_agent_name, None)

    # 移除 call_agent 工具
    if hasattr(app, 'tool_registry'):
        app.tool_registry.unregister("call_agent")

    # 更新系统提示
    app._update_system_prompt()


def _inject_chain_prompt(app, chain):
    """注入链条信息到系统提示"""
    from cbhcli_pkg.core.agent_chain import build_chain_prompt

    if not app.session or not app.current_agent_name:
        return

    chain_prompt = build_chain_prompt(
        chain, app.agent_manager, app.current_agent_name
    )

    # 追加到系统提示末尾
    if app.session.messages and app.session.messages[0].role == "system":
        # 先移除旧的链条信息（如果有）
        content = app.session.messages[0].content
        marker = "## Agent 链条信息"
        idx = content.find(marker)
        if idx > 0:
            while idx > 0 and content[idx-1] == '\n':
                idx -= 1
            content = content[:idx].rstrip()
        # 追加新的链条信息
        content = content + "\n" + chain_prompt
        app.session.messages[0].content = content
        app.session.messages[0].token_count = app.token_counter.count_tokens(content)


def _register_call_agent_tool(app, chain):
    """注册 call_agent 工具（如果当前 Agent 有下游）"""
    if not app.current_agent_name:
        return

    downstream = chain.get_downstream_agents(app.current_agent_name)
    if not downstream:
        return

    from cbhcli_pkg.tools.call_agent import CallAgentTool
    app.tool_registry.unregister("call_agent")
    app.tool_registry.register(CallAgentTool(app, chain, app.current_agent_name))

    # 更新 tools schema tokens
    if app.context_window:
        import json
        openai_tools = app.tool_registry.get_openai_tools()
        if openai_tools:
            app.context_window.tools_schema_tokens = app.token_counter.count_tokens(
                json.dumps(openai_tools, ensure_ascii=False)
            )
