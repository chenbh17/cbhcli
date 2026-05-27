"""Skills命令处理 - /skills list|add|use|deactivate|rm"""
from cbhcli_pkg.commands.parser import SlashCommand


def register_skills_commands(parser, app):
    """注册Skills相关命令"""

    def skills_handler(args):
        args = args.strip()

        if not args:
            return _skills_list(app)

        parts = args.split(None, 1)
        action = parts[0].lower()
        param = parts[1] if len(parts) > 1 else ""

        if action == "list":
            return _skills_list(app)
        elif action == "add":
            return _skills_add(app, param.strip())
        elif action == "use":
            return _skills_use(app, param.strip())
        elif action == "off":
            return _skills_deactivate(app, param.strip())
        elif action == "rm":
            if not param:
                return _show_rm_skills_menu(app)
            return _skills_rm(app, param.strip())
        else:
            return f"未知操作: {action}\n可用操作: list, add, use, off, rm"

    parser.register(SlashCommand(
        name="skills",
        description="管理Agent技能",
        usage="list|add|use|off|rm [name]",
        handler=skills_handler
    ))


def _skills_list(app):
    """列出所有技能"""
    skill_manager = getattr(app, 'skill_manager', None)
    if not skill_manager:
        return "技能管理器未初始化"

    skills = skill_manager.list_skills()
    if not skills:
        return (
            "暂无技能。\n\n"
            "创建技能的方式：\n"
            "  1. /skills add          - 交互式创建\n"
            "  2. 让AI创建             - 告诉AI你需要什么技能，AI会使用 skills_create 工具自动创建\n"
            "  3. 手动创建             - 在 skills/ 目录下创建技能文件夹\n"
        )

    active_names = skill_manager.get_active_skill_names()
    lines = ["已注册的技能:\n"]

    for skill in skills:
        is_active = skill.name in active_names
        marker = " [已激活]" if is_active else ""
        lines.append(f"  * {skill.name}{marker}")

        # 显示 skills.md 第一行作为简短描述
        first_line = skill.prompt.strip().split('\n')[0].strip()
        if first_line.startswith('#'):
            first_line = first_line.lstrip('#').strip()
        if first_line:
            lines.append(f"    {first_line}")

        if skill.has_scripts:
            scripts = skill.list_scripts()
            lines.append(f"    脚本: {', '.join(scripts)}")

        lines.append("")

    lines.append(f"共 {len(skills)} 个技能，{len(active_names)} 个已激活")
    lines.append("使用 /skills use 激活技能 | /skills off 取消激活")

    return "\n".join(lines)


def _skills_add(app, name):
    """交互式添加技能"""
    skill_manager = getattr(app, 'skill_manager', None)
    if not skill_manager:
        return "技能管理器未初始化"

    # 获取技能名称
    if not name:
        name = input("请输入技能名称 (英文，如 code-review): ").strip()
    if not name:
        return "已取消"

    # 清理名称
    clean_name = ''.join(c for c in name if c.isalnum() or c in '-_')
    if not clean_name:
        return "技能名称只能包含字母、数字、连字符和下划线"

    # 检查是否已存在
    if skill_manager.get_skill(clean_name):
        return f"技能 '{clean_name}' 已存在"

    # 获取技能描述/提示词
    print("\n请输入技能提示词内容（skills.md）:")
    print("  - 描述技能的功能、使用方法、注意事项等")
    print("  - 输入空行后按回车结束")
    print("")

    lines = []
    while True:
        try:
            line = input()
            if line == "" and lines and lines[-1] == "":
                # 连续两个空行结束
                lines.pop()  # 移除最后的空行
                break
            lines.append(line)
        except EOFError:
            break

    if not lines:
        return "已取消创建"

    prompt_content = "\n".join(lines)

    # 询问是否需要脚本
    add_script = input("\n是否需要添加脚本? (y/n, 默认n): ").strip().lower()
    scripts = {}

    if add_script == 'y':
        while True:
            script_name = input("脚本文件名 (如 run.sh，空行结束): ").strip()
            if not script_name:
                break

            print(f"请输入 {script_name} 的内容（输入空行后按回车结束）:")
            script_lines = []
            while True:
                try:
                    line = input()
                    if line == "" and script_lines and script_lines[-1] == "":
                        script_lines.pop()
                        break
                    script_lines.append(line)
                except EOFError:
                    break

            if script_lines:
                scripts[script_name] = "\n".join(script_lines)

    try:
        skill = skill_manager.create_skill(clean_name, prompt_content, scripts)
        result = f"技能 '{clean_name}' 创建成功！"
        result += f"\n路径: {skill.base_dir}"
        if skill.has_scripts:
            result += f"\n脚本: {', '.join(skill.list_scripts())}"
        result += "\n\n使用 /skills use 激活此技能"
        return result
    except Exception as e:
        return f"创建失败: {e}"


def _skills_use(app, param):
    """选择要激活的技能（支持多选）"""
    skill_manager = getattr(app, 'skill_manager', None)
    if not skill_manager:
        return "技能管理器未初始化"

    skills = skill_manager.list_skills()
    if not skills:
        return "暂无可用技能。使用 /skills add 创建技能。"

    # 如果有参数，直接激活指定技能
    if param:
        names = [n.strip() for n in param.split(',')]
        valid = []
        invalid = []
        for n in names:
            if skill_manager.get_skill(n):
                valid.append(n)
            else:
                invalid.append(n)

        if invalid:
            return f"未找到技能: {', '.join(invalid)}"

        # 先取消所有，再激活选中的
        skill_manager.deactivate_all()
        activated = skill_manager.activate_skills(valid)

        # 重建系统提示
        _rebuild_prompt(app)

        if activated:
            return f"已激活技能: {', '.join(activated)}"
        else:
            return "所选技能已全部激活"

    # 交互式多选
    active_names = skill_manager.get_active_skill_names()

    print("\n选择要激活的技能 (输入编号，多选用逗号分隔，如: 1,3,5):\n")
    for i, skill in enumerate(skills, 1):
        marker = " [*]" if skill.name in active_names else "    "
        first_line = skill.prompt.strip().split('\n')[0].strip()
        if first_line.startswith('#'):
            first_line = first_line.lstrip('#').strip()
        desc = f" - {first_line}" if first_line else ""
        print(f"  {i}. {marker} {skill.name}{desc}")

    print(f"\n  0.  取消所有激活的技能")
    print(f"  q.  取消操作")
    print("")

    choice = input("请选择 [编号]: ").strip()

    if not choice or choice.lower() == 'q':
        return "已取消"

    if choice == '0':
        skill_manager.deactivate_all()
        _rebuild_prompt(app)
        return "已取消所有技能的激活"

    # 解析选择
    selected = []
    try:
        for part in choice.split(','):
            part = part.strip()
            if '-' in part:
                # 范围选择，如 1-3
                start, end = part.split('-')
                for idx in range(int(start), int(end) + 1):
                    if 1 <= idx <= len(skills):
                        selected.append(skills[idx - 1].name)
            elif part.isdigit():
                idx = int(part)
                if 1 <= idx <= len(skills):
                    selected.append(skills[idx - 1].name)
    except (ValueError, IndexError):
        return "输入格式错误"

    if not selected:
        return "未选择任何技能"

    # 去重
    selected = list(dict.fromkeys(selected))

    # 取消所有再激活选中的
    skill_manager.deactivate_all()
    activated = skill_manager.activate_skills(selected)

    # 重建系统提示
    _rebuild_prompt(app)

    if activated:
        return f"已激活技能: {', '.join(activated)}"
    else:
        return "未激活任何技能"


def _skills_deactivate(app, param):
    """取消激活技能"""
    skill_manager = getattr(app, 'skill_manager', None)
    if not skill_manager:
        return "技能管理器未初始化"

    active_names = skill_manager.get_active_skill_names()
    if not active_names:
        return "当前没有已激活的技能"

    # 如果有参数，直接取消指定技能
    if param:
        if param == "all":
            skill_manager.deactivate_all()
            _rebuild_prompt(app)
            return "已取消所有技能的激活"

        names = [n.strip() for n in param.split(',')]
        deactivated = []
        not_found = []
        for n in names:
            if skill_manager.deactivate_skill(n):
                deactivated.append(n)
            else:
                not_found.append(n)

        _rebuild_prompt(app)

        result = []
        if deactivated:
            result.append(f"已取消激活: {', '.join(deactivated)}")
        if not_found:
            result.append(f"未找到或未激活: {', '.join(not_found)}")
        return "\n".join(result)

    # 交互式选择取消激活
    active_skills = skill_manager.get_active_skills()

    print("\n选择要取消激活的技能 (输入编号，多选用逗号分隔):\n")
    for i, skill in enumerate(active_skills, 1):
        first_line = skill.prompt.strip().split('\n')[0].strip()
        if first_line.startswith('#'):
            first_line = first_line.lstrip('#').strip()
        desc = f" - {first_line}" if first_line else ""
        print(f"  {i}. {skill.name}{desc}")

    print(f"\n  0.  取消所有")
    print(f"  q.  取消操作")
    print("")

    choice = input("请选择 [编号]: ").strip()

    if not choice or choice.lower() == 'q':
        return "已取消"

    if choice == '0':
        skill_manager.deactivate_all()
        _rebuild_prompt(app)
        return "已取消所有技能的激活"

    # 解析选择
    deactivated = []
    try:
        for part in choice.split(','):
            part = part.strip()
            if part.isdigit():
                idx = int(part)
                if 1 <= idx <= len(active_skills):
                    name = active_skills[idx - 1].name
                    skill_manager.deactivate_skill(name)
                    deactivated.append(name)
    except (ValueError, IndexError):
        return "输入格式错误"

    if not deactivated:
        return "未选择任何技能"

    _rebuild_prompt(app)
    return f"已取消激活: {', '.join(deactivated)}"


def _show_rm_skills_menu(app):
    """显示技能删除选择菜单"""
    skill_manager = getattr(app, 'skill_manager', None)
    if not skill_manager:
        return "技能管理器未初始化"

    skills = skill_manager.list_skills()
    if not skills:
        return "暂无技能。"

    lines = ["📋 选择要删除的技能 (输入编号或名称):\n"]
    for i, skill in enumerate(skills, 1):
        first_line = skill.prompt.strip().split('\n')[0].strip()
        if first_line.startswith('#'):
            first_line = first_line.lstrip('#').strip()
        desc = f" - {first_line}" if first_line else ""
        lines.append(f"  {i}. {skill.name}{desc}")
    lines.append(f"\n  0. 取消\n")

    print("\n" + "\n".join(lines))
    choice = input("请选择 [编号/名称]: ").strip()

    if not choice or choice == '0':
        return "已取消"

    if choice.isdigit():
        idx = int(choice)
        if 1 <= idx <= len(skills):
            return _skills_rm(app, skills[idx - 1].name)
        return f"❌ 无效编号 (1-{len(skills)})"

    return _skills_rm(app, choice)


def _skills_rm(app, name):
    """删除技能"""
    skill_manager = getattr(app, 'skill_manager', None)
    if not skill_manager:
        return "技能管理器未初始化"

    skill = skill_manager.get_skill(name)
    if not skill:
        return f"未找到技能: {name}"

    confirm = input(f"确定要删除技能 '{name}' 吗? (y/n): ").strip().lower()
    if confirm != 'y':
        return "已取消"

    if skill_manager.remove_skill(name):
        # 如果删除的是已激活技能，重建提示
        _rebuild_prompt(app)
        return f"技能 '{name}' 已删除"
    else:
        return f"删除失败"


def _rebuild_prompt(app):
    """重建系统提示（加入激活技能的内容，保留对话历史）"""
    try:
        app._update_system_prompt()
    except Exception:
        pass
