"""工具开关命令处理 - 管理当前Agent可用的内置工具"""
import json
from pathlib import Path
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
    ("delegate_task",   "委托子任务给子Agent",          "任务管理"),
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
    lines = ["🔧 选择要开启的工具（可多选，用逗号分隔编号）:\n"]
    available = []
    for i, name in enumerate(disabled, 1):
        desc = TOOL_NAMES.get(name, name)
        available.append(name)
        lines.append(f"  {i}. {name:18s} {desc}")

    lines.append("")
    lines.append(f"  0. 取消")
    lines.append("")

    print("\n".join(lines))
    choice = input("请选择编号（多个用逗号分隔）: ").strip()

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
    lines = ["🔧 选择要关闭的工具（可多选，用逗号分隔编号）:\n"]
    for i, name in enumerate(enabled, 1):
        desc = TOOL_NAMES.get(name, name)
        lines.append(f"  {i}. {name:18s} {desc}")

    lines.append("")
    lines.append(f"  0. 取消")
    lines.append("")

    print("\n".join(lines))
    choice = input("请选择编号（多个用逗号分隔）: ").strip()

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
    """解析多选输入（逗号分隔的编号）

    Returns:
        选中的索引列表（0-based），无效输入返回 None
    """
    indices = []
    parts = [p.strip() for p in choice.split(",")]

    for part in parts:
        if not part:
            continue
        if not part.isdigit():
            return None
        idx = int(part)
        if idx < 1 or idx > max_idx:
            return None
        # 转为0-based索引，去重
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
    """生成并写入 tools.md"""
    lines = [
        "# 工具使用指南",
        "",
        "## 重要说明",
        "所有工具通过 OpenAI Function Calling 协议自动调用，你只需在需要时调用工具即可。",
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
        "- 如果不先读取文件，你无法知道文件的真实内容，edit 将会失败",
        "- 正确流程：先 read 读取文件 → 确认要修改的内容 → 再 edit 替换",
        "",
    ]

    # 工具详细描述（按分类）
    tool_details = {
        "terminal": (
            "### terminal - 执行终端命令",
            "用于执行任何shell命令，如 ls, cat, grep, git 等。\n"
            "- command: 要执行的命令字符串\n"
            "- 避免执行危险命令如 rm -rf /\n"
            "- 复杂命令可以组合使用 && 或 |"
        ),
        "read": (
            "### read - 读取文件内容",
            "用于读取文件内容，支持指定行范围。\n"
            "- file_path: 文件绝对路径（必填）\n"
            "- start_line / end_line: 行范围（可选）"
        ),
        "write": (
            "### write - 写入文件",
            "创建新文件或覆盖现有文件。⚠️ 会完全覆盖现有内容！\n"
            "- file_path: 文件绝对路径（必填）\n"
            "- content: 文件内容（必填）"
        ),
        "edit": (
            "### edit - 编辑文件",
            "精确替换文件中的文本。\n"
            "- file_path: 文件绝对路径（必填）\n"
            "- old_str: 要替换的原文本（必须唯一匹配）\n"
            "- new_str: 替换后的新文本"
        ),
        "grep": (
            "### grep - 正则搜索文件内容",
            "基于正则表达式搜索文件内容，返回匹配的文件名、行号和内容。\n"
            "- pattern: 正则表达式（必填）\n"
            "- path: 搜索路径，默认当前目录\n"
            "- include: 文件名过滤（glob格式），如 \"*.py\"\n"
            "- ignore_case: 是否忽略大小写，默认 false\n"
            "- context_lines: 匹配行前后上下文行数，默认 0\n"
            "- max_results: 最大返回结果数，默认 50"
        ),
        "glob": (
            "### glob - 文件模式匹配搜索",
            "按 glob 模式搜索文件路径，快速定位文件。\n"
            "- pattern: Glob 模式（必填），如 \"**/*.py\"\n"
            "- path: 搜索起始目录，默认当前目录\n"
            "- max_results: 最大返回结果数，默认 100"
        ),
        "ask_user": (
            "### ask_user - 向用户提问",
            "当需求不明确或需要用户决策时，向用户提问并提供选项。\n"
            "- question: 问题内容（必填）\n"
            "- options: 选项列表（可选）\n"
            "- allow_multiple: 是否允许多选，默认 false\n"
            "- 应在关键决策点使用，避免频繁打断用户"
        ),
        "Todo": (
            "### Todo - 管理任务计划列表",
            "当任务涉及多个步骤时，用于创建和追踪任务计划。\n"
            "- todos: 完整的任务列表数组，每项包含 content(描述) 和 status(pending/in_progress/completed)\n"
            "- 每次调用需传入完整列表（所有条目及最新状态）\n"
            "- 复杂任务开始前先创建计划，每完成一个步骤更新状态"
        ),
        "python": (
            "### python - 执行Python代码",
            "用于执行Python代码，支持数据处理、计算、API调用等。\n"
            "- code: Python代码字符串\n"
            "- 同一会话中变量和导入的模块会保留（会话记忆）\n"
            "- 使用 /reset 或 /new 创建新会话后变量记忆被清空"
        ),
        "memory_search": (
            "### memory_search - 语义搜索向量化知识",
            "- query: 搜索内容（必填）\n"
            "- top_k: 返回结果数，默认 5\n"
            "- 需要先配置嵌入模型: /model embedding add"
        ),
        "knowledge_base": (
            "### knowledge_base - 查询知识库",
            "- query: 查询内容（必填）\n"
            "- top_k: 返回结果数，默认 5"
        ),
        "skills_create": (
            "### skills_create - 创建新技能",
            "在当前Agent工作空间的skills文件夹下创建新技能。\n"
            "- skill_name: 技能名称（字母、数字、连字符、下划线）\n"
            "- prompt_content: 技能提示词内容\n"
            "- scripts: 可选脚本字典"
        ),
        "delegate_task": (
            "### delegate_task - 委托子任务给子Agent",
            "将独立子任务委托给子Agent执行，子Agent拥有独立会话上下文。\n"
            "- task: 子任务描述（必填）\n"
            "- context: 额外上下文（可选）\n"
            "- 子Agent无法访问当前对话历史，请在task中提供完整信息"
        ),
        # --- cbhpacks 数据科学工具 ---
        "cbhpacks_bins_model": (
            "### cbhpacks_bins_model - 分箱WOE/IV/PSI",
            "cbhpacks分箱模型工具。对特征进行分箱、WOE转换、IV计算、PSI稳定性检验。\n"
            "- **状态缓存**: 同一数据集多次调用共享状态（comp_woe_iv后可直接调data_to_woe）\n"
            "- **自动分目录**: 不同bins_type自动分目录，新实例自动创建独立目录\n"
            "- **自动保存源码**: 每次执行产出 run_{method}.py 可复现脚本\n"
            "- method: comp_woe_iv/bins_rpt/data_to_woe/get_psi/psi_mth_avg/plot_col_rpt/plot_cols_rpt（必填）\n"
            "- csv_path/cols/target/group/nan/bins_type/output_path\n"
            "- 产出iv_data.csv供cols_select的iv_select/corr_select使用"
        ),
        "cbhpacks_binary_model": (
            "### cbhpacks_binary_model - 二分类模型训练评估",
            "cbhpacks二分类模型训练工具。支持LR/XGB/LGBM/MLP/SVM/RDF训练、调参、评估报告。\n"
            "- **自动加载模型**: 调参/报告方法自动从pkl加载已训练模型\n"
            "- **自动保存源码**: 每次执行产出 run_{method}.py 可复现脚本\n"
            "- lgbm_fit默认verbose=-1静默警告\n"
            "- method: lr_fit/xgb_fit/lgbm_fit/mlp_fit/svm_fit/rdf_fit/para_adj_gs/para_adj_bs/report（必填）\n"
            "- train_csv/cols/target/fit_params/model_path\n"
            "- report需要: group/mth_col/base_mth"
        ),
        "cbhpacks_uns_model": (
            "### cbhpacks_uns_model - 无监督学习PCA/聚类",
            "cbhpacks无监督学习工具。PCA主成分分析 + KMeans聚类。\n"
            "- **自动保存源码**: 每次执行产出 run_{method}.py 可复现脚本\n"
            "- method: pca/get_keams_cluster/kmeans（必填）\n"
            "- csv_path/cols/target/path\n"
            "- pca: var_ratio_cumsum(默认0.8)\n"
            "- kmeans: n_clusters(必填)"
        ),
        "cbhpacks_linear_model": (
            "### cbhpacks_linear_model - 线性回归/工具变量",
            "cbhpacks线性回归工具。OLS/Logit回归 + 工具变量回归(IV)。\n"
            "- **自动保存源码**: 每次执行产出 run_{method}.py 可复现脚本\n"
            "- method: ols/IV（必填）\n"
            "- csv_path/cols/target/path\n"
            "- IV需要: iv_target/iv_col"
        ),
        "cbhpacks_cols_select": (
            "### cbhpacks_cols_select - 特征筛选(10种方法)",
            "cbhpacks通用特征筛选工具。10种筛选方法逐步筛选特征。\n"
            "- **状态缓存**: 同一数据集多次调用共享状态，cols_s逐步缩减\n"
            "- **自动分目录**: 新实例自动创建独立目录\n"
            "- **自动保存源码**: 每次执行产出 run_{method}.py 可复现脚本\n"
            "- method: null_select/enumerate_select/iv_select/psi_select/corr_select/chi2_select/logistic_select/ml_select/boostrap_select/vif_select（必填）\n"
            "- csv_path/cols/target/path\n"
            "- iv_select/corr_select需要iv_data_csv，psi_select需要psi_data_csv\n"
            "- reset=true可重置筛选状态"
        ),
        "cbhpacks_cols_select_js": (
            "### cbhpacks_cols_select_js - 递归特征筛选",
            "cbhpacks递归特征筛选工具。递归迭代剔除低重要性特征。\n"
            "- **自动保存源码**: 执行产出 run_recursion_select.py 可复现脚本\n"
            "- train_csv/test_csv/cols/target\n"
            "- method_type: xgb/lgb（默认lgb）\n"
            "- recursion_num(默认30)/stay_pct(默认0.95)"
        ),
        "cbhpacks_cols_encode": (
            "### cbhpacks_cols_encode - 特征编码(7种方法)",
            "cbhpacks特征编码工具。7种编码方法。\n"
            "- **自动分目录**: 目录已有文件时自动创建新目录\n"
            "- **自动保存源码**: 每次执行产出 run_{method}.py 可复现脚本\n"
            "- method: data_to_sigmoid/data_to_sc/data_to_minmax/data_to_softmax/bins_to_num/str_to_num/data_to_woe（必填）\n"
            "- csv_path/cols/target/bins_type/group/path"
        ),
        "cbhpacks_cols_operate": (
            "### cbhpacks_cols_operate - 列操作",
            "cbhpacks列操作工具。6种列操作方法。\n"
            "- **自动保存源码**: 每次执行产出 run_{method}.py 可复现脚本\n"
            "- method: col_explode/col_to_T/col_to_cols/date_col_trans/date_mth_year/jieba_trans（必填）\n"
            "- csv_path/col/mean_key/date_col/output_csv"
        ),
        "cbhpacks_desc_df": (
            "### cbhpacks_desc_df - 数据集描述统计",
            "cbhpacks数据集描述统计工具。生成数值型和类别型特征的描述统计报告。\n"
            "- **自动保存源码**: 执行产出 run_get_rpt.py 可复现脚本\n"
            "- csv_path/cols/cat_cols/path\n"
            "- 输出: desc_num_rpt.xlsx, desc_cat_rpt.xlsx"
        ),
        "cbhpacks_desc_col": (
            "### cbhpacks_desc_col - 单变量分析/异常值检测",
            "cbhpacks单变量分析工具。描述性分析、相关性分析、有监督分析、异常值检测。\n"
            "- **自动分目录**: 目录已有文件时自动创建新目录\n"
            "- **自动保存源码**: 每次执行产出 run_{method}.py 可复现脚本\n"
            "- method: desc_/relative_/supervised_/easy_od/feat_card（必填）\n"
            "- csv_path/col/cols/target/how(whisker/3sigma)/path"
        ),
        "cbhpacks_con_sql": (
            "### cbhpacks_con_sql - 数据库连接SQL执行",
            "cbhpacks数据库连接工具。ClickHouse/MySQL/Hive SQL执行与数据导入。\n"
            "- method: chrun/chdf/con_mysql/con_hive/get_create_table/to_hive/rfms_sql（必填）\n"
            "- sql/csv_path/table_name/host/port/user/password/database"
        ),
        "cbhpacks_con_linux": (
            "### cbhpacks_con_linux - Linux SSH连接命令",
            "cbhpacks Linux连接工具。SSH执行命令、文件传输、Hadoop/Hive管理。\n"
            "- method: con_linux/data_trans_linux/jps/hadoop/start_hive（必填）\n"
            "- shell/user/local_loc/client_loc"
        ),
        "cbhpacks_get_random_data": (
            "### cbhpacks_get_random_data - 生成随机测试数据",
            "cbhpacks测试数据生成工具。生成包含月份、特征、目标变量的随机数据集。\n"
            "- min_edge/max_edge(默认0~100)/num(默认1000)/mth_cnt(默认6)\n"
            "- output_csv(默认test_random_data.csv)\n"
            "- 产出的CSV可供所有cbhpacks工具使用"
        ),
    }

    # 只输出已启用的工具
    lines.append("## 可用工具")
    lines.append("")
    has_enabled = False
    for name, desc, category in BUILTIN_TOOLS:
        if name not in disabled:
            has_enabled = True
            title, detail = tool_details[name]
            lines.append(detail)
            lines.append("")

    if not has_enabled:
        lines.append("⚠️ 当前没有可用的工具，请使用 `/tools on` 开启工具。")
        lines.append("")

    # MCP工具提示
    lines.append("### MCP 工具 - 外部服务器扩展工具")
    lines.append("通过 MCP 协议连接的外部工具，名称格式为 mcp_服务器名_工具名。")
    lines.append("- 用户通过 /mcp add 命令添加")
    lines.append("- 使用 /mcp tools 服务器名 查看详细参数")
    lines.append("")

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
    lines.append("- 执行命令前先解释意图")
    lines.append("- 重要操作前提醒用户")
    lines.append("- 出错时提供解决方案")

    tools_md = "\n".join(lines)
    tools_path = workspace / "tools.md"
    tools_path.write_text(tools_md, encoding="utf-8")


def _write_usage_md(workspace: Path, disabled: list) -> None:
    """更新 usage.md 中的可用工具部分"""
    usage_path = workspace / "usage.md"
    if not usage_path.exists():
        return

    content = usage_path.read_text(encoding="utf-8")

    # 构建新的可用工具部分
    tool_lines = []
    for name, desc, category in BUILTIN_TOOLS:
        if name not in disabled:
            if name == "edit":
                tool_lines.append(f"- {name}: {desc}（**必须先用 read 读取文件后才能使用！**）")
            elif name == "Todo":
                tool_lines.append(f"- {name}: {desc}（**每个任务必须优先使用，先规划再执行**）")
            elif name == "python":
                tool_lines.append(f"- {name}: {desc}（带会话记忆）")
            elif name == "memory_search":
                tool_lines.append(f"- {name}: 语义搜索向量化知识内容")
            elif name == "delegate_task":
                tool_lines.append(f"- {name}: 将独立子任务委托给子Agent执行")
            else:
                tool_lines.append(f"- {name}: {desc}")

    tool_lines.append("- mcp_*: MCP工具服务器提供的扩展工具")

    new_tools_section = "\n".join(tool_lines)

    # 替换 "## 可用工具（通过 Function Calling 自动调用）" 到下一个 "## " 之间的内容
    import re
    pattern = r'(## 可用工具（通过 Function Calling 自动调用）\n)(.*?)(\n## )'
    replacement = f'\\1{new_tools_section}\n\\3'

    new_content, count = re.subn(pattern, replacement, content, count=1, flags=re.DOTALL)
    if count > 0:
        usage_path.write_text(new_content, encoding="utf-8")
