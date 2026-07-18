"""备用模型管理命令 - 配置主模型和视觉模型的备用切换顺序"""
from cbhcli_pkg.core.prompt_utils import ask_text
from cbhcli_pkg.commands.parser import SlashCommand
from cbhcli_pkg.config.global_config import GlobalConfig


def register_fallback_commands(parser, app):
    """注册备用模型管理命令"""

    def fallback_handler(args):
        args = args.strip()

        if not args:
            return (
                "🔄 备用模型管理命令:\n"
                "  /fallback add [main|vision] <模型名>  - 添加备用模型\n"
                "  /fallback list                       - 查看备用模型配置\n"
                "  /fallback rm [main|vision] <模型名>  - 移除备用模型\n"
                "  /fallback reorder [main|vision]      - 重新排序备用模型\n"
                "  /fallback clear [main|vision]        - 清空备用模型列表\n"
                "\n"
                "  main   - 主模型备用（主模型断网/异常时自动切换）\n"
                "  vision - 视觉模型备用（image工具的视觉模型不可用时切换）"
            )

        parts = args.split()
        action = parts[0].lower()

        if action == "list":
            return _list_fallback(app)

        elif action == "add":
            if len(parts) < 3:
                return "❌ 用法: /fallback add [main|vision] <模型名>"
            category = parts[1].lower()
            model_name = parts[2]
            return _add_fallback(app, category, model_name)

        elif action == "rm":
            if len(parts) < 3:
                return "❌ 用法: /fallback rm [main|vision] <模型名>"
            category = parts[1].lower()
            model_name = parts[2]
            return _rm_fallback(app, category, model_name)

        elif action == "reorder":
            if len(parts) < 2:
                return "❌ 用法: /fallback reorder [main|vision]"
            category = parts[1].lower()
            return _reorder_fallback(app, category)

        elif action == "clear":
            if len(parts) < 2:
                return "❌ 用法: /fallback clear [main|vision]"
            category = parts[1].lower()
            return _clear_fallback(app, category)

        else:
            return f"❌ 未知操作: {action}\n可用子命令: list, add, rm, reorder, clear"

    parser.register(SlashCommand(
        name="fallback",
        description="管理备用模型（主模型/视觉模型的自动切换）",
        usage="add|list|rm|reorder|clear [main|vision] <模型名>",
        handler=fallback_handler,
        requires_agent=False
    ))


def _validate_category(category: str) -> tuple[bool, str]:
    """验证类别参数"""
    if category not in ("main", "vision"):
        return False, f"❌ 无效类别: {category}\n可用类别: main（主模型）, vision（视觉模型）"
    return True, ""


def _list_fallback(app) -> str:
    """列出备用模型配置"""
    config = GlobalConfig()
    models = config.get_models()
    main_fallback = config.get_fallback_models()
    vision_fallback = config.get_fallback_vision_models()

    lines = ["🔄 备用模型配置:\n"]

    # 主模型备用列表
    lines.append("  【主模型备用】")
    if main_fallback:
        for i, name in enumerate(main_fallback, 1):
            model = config.get_model(name)
            status = "✅" if model else "❌(未配置)"
            lines.append(f"    {i}. {status} {name}")
    else:
        lines.append("    （未配置）")
    lines.append("")

    # 视觉模型备用列表
    lines.append("  【视觉模型备用】")
    if vision_fallback:
        for i, name in enumerate(vision_fallback, 1):
            model = config.get_model(name)
            status = "✅" if model else "❌(未配置)"
            vision_tag = " [视觉]" if model and model.get("vision") else ""
            lines.append(f"    {i}. {status} {name}{vision_tag}")
    else:
        lines.append("    （未配置）")
    lines.append("")

    # 显示所有可用模型
    lines.append("  【所有已配置模型】")
    if models:
        for m in models:
            name = m.get("name", "?")
            vision = " [视觉]" if m.get("vision") else ""
            in_main = " → 主备用" if name in main_fallback else ""
            in_vision = " → 视觉备用" if name in vision_fallback else ""
            lines.append(f"    • {name}{vision}{in_main}{in_vision}")
    else:
        lines.append("    （无已配置模型）")

    return "\n".join(lines)


def _add_fallback(app, category: str, model_name: str) -> str:
    """添加备用模型"""
    ok, msg = _validate_category(category)
    if not ok:
        return msg

    config = GlobalConfig()

    # 检查模型是否存在
    model = config.get_model(model_name)
    if not model:
        return f"❌ 模型 '{model_name}' 不存在，请先使用 /model add 添加"

    # 视觉模型检查
    if category == "vision" and not model.get("vision", False):
        return f"❌ 模型 '{model_name}' 不支持视觉功能，请添加时选择支持视觉 (y)"

    if category == "main":
        fallback_list = config.get_fallback_models()
    else:
        fallback_list = config.get_fallback_vision_models()

    if model_name in fallback_list:
        return f"⚠️  模型 '{model_name}' 已在{category}备用列表中"

    fallback_list.append(model_name)

    if category == "main":
        config.set_fallback_models(fallback_list)
    else:
        config.set_fallback_vision_models(fallback_list)

    position = len(fallback_list)
    return f"✅ 已添加 '{model_name}' 到{category}备用列表（第{position}位）"


def _rm_fallback(app, category: str, model_name: str) -> str:
    """移除备用模型"""
    ok, msg = _validate_category(category)
    if not ok:
        return msg

    config = GlobalConfig()

    if category == "main":
        fallback_list = config.get_fallback_models()
    else:
        fallback_list = config.get_fallback_vision_models()

    if model_name not in fallback_list:
        return f"❌ 模型 '{model_name}' 不在{category}备用列表中"

    fallback_list.remove(model_name)

    if category == "main":
        config.set_fallback_models(fallback_list)
    else:
        config.set_fallback_vision_models(fallback_list)

    return f"✅ 已从{category}备用列表移除 '{model_name}'"


def _reorder_fallback(app, category: str) -> str:
    """交互式重新排序备用模型"""
    ok, msg = _validate_category(category)
    if not ok:
        return msg

    config = GlobalConfig()

    if category == "main":
        fallback_list = config.get_fallback_models()
        setter = config.set_fallback_models
    else:
        fallback_list = config.get_fallback_vision_models()
        setter = config.set_fallback_vision_models

    if not fallback_list:
        return f"❌ {category}备用列表为空，无可排序的模型"

    # 显示当前顺序
    lines = [f"🔄 当前{category}备用模型顺序:\n"]
    for i, name in enumerate(fallback_list, 1):
        lines.append(f"  {i}. {name}")
    lines.append("")
    lines.append("请输入新的顺序（用逗号分隔编号，如 2,1,3）:")
    print("\n".join(lines))

    choice = ask_text("新顺序: ").strip()
    if not choice:
        return "已取消"

    try:
        indices = [int(x.strip()) for x in choice.split(",")]
    except ValueError:
        return "❌ 无效输入，请输入数字编号"

    if len(indices) != len(fallback_list):
        return f"❌ 需要输入 {len(fallback_list)} 个编号"

    if sorted(indices) != list(range(1, len(fallback_list) + 1)):
        return "❌ 编号不完整或有重复"

    new_order = [fallback_list[i - 1] for i in indices]
    setter(new_order)

    result_lines = [f"✅ {category}备用模型顺序已更新:\n"]
    for i, name in enumerate(new_order, 1):
        result_lines.append(f"  {i}. {name}")
    return "\n".join(result_lines)


def _clear_fallback(app, category: str) -> str:
    """清空备用模型列表"""
    ok, msg = _validate_category(category)
    if not ok:
        return msg

    config = GlobalConfig()

    if category == "main":
        if not config.get_fallback_models():
            return "⚠️  主模型备用列表已为空"
        config.set_fallback_models([])
    else:
        if not config.get_fallback_vision_models():
            return "⚠️  视觉模型备用列表已为空"
        config.set_fallback_vision_models([])

    return f"✅ 已清空{category}备用模型列表"