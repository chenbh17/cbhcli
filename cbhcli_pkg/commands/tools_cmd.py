"""工具开关命令处理 - 管理当前Agent可用的内置工具"""
import json
from pathlib import Path
from cbhcli_pkg.core.prompt_utils import ask_text
from cbhcli_pkg.commands.parser import SlashCommand


# 内置工具定义：(工具名, 中文描述, 分类)
BUILTIN_TOOLS = [
    ("terminal",        "执行终端命令",                 "文件操作"),
    ("read",            "读取文件内容",                 "文件操作"),
    ("write",           "写入文件",                     "文件操作"),
    ("edit",            "精确替换文件内容",             "文件操作"),
    ("grep",            "正则搜索文件内容",             "文件操作"),
    ("glob",            "文件模式匹配搜索",             "文件操作"),
    ("python",          "执行Python代码",              "代码执行"),
    ("Todo",            "管理任务计划列表",             "任务管理"),
    ("ask_user",        "向用户提问并提供选项",         "交互"),
    ("memory_search",   "语义搜索向量化知识",           "知识检索"),
    ("knowledge_base",  "查询知识库内容",               "知识检索"),
    ("skills_create",   "创建新技能",                   "技能系统"),
    ("delegate_task",   "委托子任务给子Agent(可并行)",   "任务管理"),
    ("image",           "使用视觉模型识别图片内容",      "图片识别"),
    # cbhpacks 数据科学工具（默认关闭）
    ("cbhpacks_bins_model",       "分箱WOE/IV/PSI计算",      "数据科学"),
    ("cbhpacks_binary_model",     "二分类模型训练评估",        "数据科学"),
    ("cbhpacks_uns_model",        "无监督学习PCA/聚类",        "数据科学"),
    ("cbhpacks_linear_model",     "线性回归/工具变量",        "数据科学"),
    ("cbhpacks_cols_select",      "特征筛选(10种方法)",       "数据科学"),
    ("cbhpacks_cols_select_js",   "递归特征筛选",             "数据科学"),
    ("cbhpacks_cols_encode",      "特征编码(7种方法)",        "数据科学"),
    ("cbhpacks_cols_operate",     "列操作(炸裂/转置/分词)",   "数据科学"),
    ("cbhpacks_desc_df",          "数据集描述统计",           "数据科学"),
    ("cbhpacks_desc_col",         "单变量分析/异常值检测",    "数据科学"),
    ("cbhpacks_con_sql",          "数据库连接SQL执行",        "数据科学"),
    ("cbhpacks_con_linux",        "Linux SSH连接命令",        "数据科学"),
    ("cbhpacks_get_random_data",  "生成随机测试数据",         "数据科学"),
]

# 工具名到中文描述的映射
TOOL_NAMES = {t[0]: t[1] for t in BUILTIN_TOOLS}
ALL_TOOL_NAMES = [t[0] for t in BUILTIN_TOOLS]


def register_tools_commands(parser, app):
    """注册工具开关命令"""

    def tools_handler(args):
        args = args.strip()

        if not args:
            return (
                "🔧 工具管理命令:\n"
                "  /tools list  - 查看所有工具状态\n"
                "  /tools on    - 开启工具（交互式多选）\n"
                "  /tools off   - 关闭工具（交互式多选）\n"
            )

        parts = args.split(None, 1)
        action = parts[0].lower()

        if action == "list":
            return _list_tools(app)
        elif action == "on":
            return _tools_on(app)
        elif action == "off":
            return _tools_off(app)
        else:
            return f"❌ 未知操作: {action}\n可用子命令: list, on, off"

    parser.register(SlashCommand(
        name="tools",
        description="管理当前Agent的工具开关",
        usage="list|on|off",
        handler=tools_handler,
        requires_agent=True
    ))


def _get_disabled_tools(app) -> list:
    """获取当前Agent被禁用的工具列表"""
    if not app.current_agent_config:
        return []
    return app.current_agent_config.disabled_tools or []


def _save_disabled_tools(app, disabled: list) -> None:
    """保存当前Agent被禁用的工具列表，并实时刷新工具注册中心和系统提示"""
    if not app.current_agent_config:
        return
    app.current_agent_config.disabled_tools = disabled
    app.agent_manager._save_config(app.current_agent_config)

    # 实时更新工具注册中心的禁用列表
    app.tool_registry.set_disabled_tools(disabled)

    # 实时刷新系统提示和 tools schema，无需重启即可生效
    app._update_system_prompt()


def _list_tools(app) -> str:
    """列出所有工具及其开关状态"""
    disabled = _get_disabled_tools(app)
    agent_name = app.current_agent_name or "未知"

    lines = [f"🔧 Agent '{agent_name}' 的工具状态:\n"]

    # 按分类分组显示
    categories = {}
    for name, desc, category in BUILTIN_TOOLS:
        categories.setdefault(category, []).append((name, desc))

    for cat, tools in categories.items():
        lines.append(f"  【{cat}】")
        for name, desc in tools:
            if name in disabled:
                lines.append(f"    ❌ {name:18s} {desc}")
            else:
                lines.append(f"    ✅ {name:18s} {desc}")
        lines.append("")

    enabled_count = len(BUILTIN_TOOLS) - len(disabled)
    lines.append(f"  共 {len(BUILTIN_TOOLS)} 个工具，已启用 {enabled_count} 个，已禁用 {len(disabled)} 个")

    if disabled:
        lines.append(f"\n  💡 使用 /tools on 可重新启用已禁用的工具")
    else:
        lines.append(f"\n  💡 所有工具均已启用。使用 /tools off 可禁用不需要的工具")

    return "\n".join(lines)


def _tools_on(app) -> str:
    """开启工具（交互式多选）"""
    disabled = _get_disabled_tools(app)

    if not disabled:
        return "✅ 所有工具均已启用，没有需要开启的工具"

    # 显示可开启的工具列表
    lines = ["🔧 选择要开启的工具（可多选，用逗号分隔编号，支持范围如 1-5）:\n"]
    available = []
    for i, name in enumerate(disabled, 1):
        desc = TOOL_NAMES.get(name, name)
        available.append(name)
        lines.append(f"  {i}. {name:18s} {desc}")

    lines.append("")
    lines.append(f"  0. 取消")
    lines.append("")

    print("\n".join(lines))
    choice = ask_text("请选择编号（多个用逗号分隔，支持范围如 1-5）: ").strip()

    if not choice or choice == '0':
        return "已取消"

    # 解析用户选择
    selected = _parse_multi_choice(choice, len(available))
    if selected is None:
        return "❌ 无效输入"

    if not selected:
        return "❌ 未选择任何工具"

    # 更新禁用列表
    tools_to_enable = [available[i] for i in selected]
    new_disabled = [t for t in disabled if t not in tools_to_enable]

    _save_disabled_tools(app, new_disabled)
    _update_agent_docs(app)

    enabled_names = ", ".join(tools_to_enable)
    remaining = len(new_disabled)
    msg = f"✅ 已开启 {len(tools_to_enable)} 个工具: {enabled_names}"
    if remaining > 0:
        msg += f"\n   仍有 {remaining} 个工具处于禁用状态"
    else:
        msg += "\n   所有工具均已启用！"
    return msg


def _tools_off(app) -> str:
    """关闭工具（交互式多选）"""
    disabled = _get_disabled_tools(app)
    enabled = [t for t in ALL_TOOL_NAMES if t not in disabled]

    if not enabled:
        return "❌ 所有工具均已禁用，没有可关闭的工具"

    # 显示可关闭的工具列表
    lines = ["🔧 选择要关闭的工具（可多选，用逗号分隔编号，支持范围如 1-5）:\n"]
    for i, name in enumerate(enabled, 1):
        desc = TOOL_NAMES.get(name, name)
        lines.append(f"  {i}. {name:18s} {desc}")

    lines.append("")
    lines.append(f"  0. 取消")
    lines.append("")

    print("\n".join(lines))
    choice = ask_text("请选择编号（多个用逗号分隔，支持范围如 1-5）: ").strip()

    if not choice or choice == '0':
        return "已取消"

    # 解析用户选择
    selected = _parse_multi_choice(choice, len(enabled))
    if selected is None:
        return "❌ 无效输入"

    if not selected:
        return "❌ 未选择任何工具"

    # 更新禁用列表
    tools_to_disable = [enabled[i] for i in selected]
    new_disabled = disabled + tools_to_disable

    _save_disabled_tools(app, new_disabled)
    _update_agent_docs(app)

    disabled_names = ", ".join(tools_to_disable)
    msg = f"✅ 已关闭 {len(tools_to_disable)} 个工具: {disabled_names}"
    msg += f"\n   使用 /tools list 查看当前状态，/tools on 可重新开启"
    return msg


def _parse_multi_choice(choice: str, max_idx: int) -> list:
    """解析多选输入（逗号分隔的编号，支持范围如 1-5、1-5,7,8-10）

    Returns:
        选中的索引列表（0-based），无效输入返回 None
    """
    indices = []
    parts = [p.strip() for p in choice.split(",")]

    for part in parts:
        if not part:
            continue
        if '-' in part:
            # 范围语法: 1-5, 3-7 等
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


def _update_agent_docs(app) -> None:
    """更新当前Agent工作空间的 tools.md 和 usage.md"""
    if not app.current_agent_config:
        return

    workspace = app.current_agent_config.workspace_path
    disabled = _get_disabled_tools(app)

    # 更新 tools.md
    _write_tools_md(workspace, disabled)

    # 更新 usage.md
    _write_usage_md(workspace, disabled)


def _write_tools_md(workspace: Path, disabled: list) -> None:
    """生成并写入 tools.md（精简版）"""
    lines = [
        "# 工具使用指南",
        "",
        "## 核心工作流程（必须遵守！）",
        "",
        "### 1. 每个任务必须先用 Todo 工具做规划",
        "无论任务简单还是复杂，收到用户请求后第一步都是调用 Todo 工具创建任务计划：",
        "- 将任务拆分为清晰的步骤",
        "- 每个步骤设为 pending 状态",
        "- 开始执行某步骤前标记为 in_progress",
        "- 完成后标记为 completed",
        "- 每次调用 Todo 都传入完整列表（所有条目及最新状态）",
        "",
        "### 2. 使用 edit 工具前必须先用 read 工具读取文件",
        "**禁止在未读取文件的情况下直接使用 edit 工具！**",
        "- edit 的 old_str 必须与文件实际内容完全一致（包括缩进和空白）",
        "- 正确流程：先 read 读取文件 → 确认要修改的内容 → 再 edit 替换",
        "",
        "## 工具调用说明",
        "所有工具的详细参数定义通过 API 的 Function Calling 协议自动获取，你只需根据参数 schema 正确传参即可。",
        "MCP 扩展工具名称格式为 `mcp_服务器名_工具名`，使用方式与内置工具完全相同。",
        "",
    ]

    # 如果有禁用的工具，列出
    if disabled:
        lines.append("## 已禁用的工具")
        lines.append("")
        lines.append("以下工具已被管理员禁用，无法使用：")
        lines.append("")
        for name in disabled:
            desc = TOOL_NAMES.get(name, name)
            lines.append(f"- ~~{name}~~: {desc}")
        lines.append("")
        lines.append("使用 `/tools on` 可重新启用这些工具。")
        lines.append("")

    lines.append("## 最佳实践")
    lines.append("- **每个任务第一步调用 Todo 工具创建计划**，然后按计划逐步执行")
    lines.append("- **edit 前必须先 read**，确认文件内容后再精确替换")
    lines.append("- 使用 grep/glob 快速定位文件和内容，避免盲目读取大量文件")
    lines.append("- 在需求不明确时使用 ask_user 向用户确认，而不是猜测")
    lines.append("- 重要操作前提醒用户")
    lines.append("- 出错时提供解决方案")
    lines.append("- **需要识别图片时使用 image 工具**，传入图片路径和识别需求，工具会自动调用视觉模型识别图片内容")
    lines.append("- **有多个相互独立的子任务时使用 delegate_task 传入 tasks 列表并行委托**，全部子Agent完成后主Agent再继续，可显著缩短总耗时")

    tools_md = "\n".join(lines)
    tools_path = workspace / "tools.md"
    tools_path.write_text(tools_md, encoding="utf-8")


def _write_usage_md(workspace: Path, disabled: list) -> None:
    """usage.md 精简版不再包含工具列表，无需更新"""
    pass
