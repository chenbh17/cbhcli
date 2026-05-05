"""模型配置命令处理"""
from cbhcli_pkg.commands.parser import SlashCommand


def register_model_commands(parser, app):
    """注册模型相关命令"""

    # /model 命令
    def model_handler(args):
        args = args.strip()

        if not args:
            return (
                "📋 模型配置命令:\n"
                "  /model add          - 添加新模型\n"
                "  /model list         - 列出所有模型\n"
                "  /model use [name]   - 使用指定模型\n"
                "  /model rm <name>    - 删除模型\n"
                "  /model info         - 查看当前模型信息\n"
                "  /model embedding    - 配置嵌入模型\n"
                "  /model rerank       - 配置重排序模型"
            )

        parts = args.split(None, 1)
        action = parts[0].lower()
        param = parts[1] if len(parts) > 1 else ""

        if action == "add":
            return _add_model(app)

        elif action == "list":
            return _list_models(app)

        elif action == "use":
            if not param:
                return _show_model_menu(app)
            return _use_model(app, param.strip())

        elif action == "rm":
            if not param:
                return _show_rm_model_menu(app)
            return _delete_model(app, param.strip())

        elif action == "info":
            return _model_info(app)

        elif action == "embedding":
            return _embedding_model_menu(app, param)

        elif action == "rerank":
            return _rerank_model_menu(app, param)

        else:
            return f"❌ 未知操作: {action}"

    parser.register(SlashCommand(
        name="model",
        description="管理模型配置",
        usage="add|list|use|rm|info|embedding|rerank [name]",
        handler=model_handler
    ))


def _add_model(app):
    """添加新模型"""
    print("\n--- 添加新模型 ---")
    name = input("模型名称: ").strip()
    if not name:
        return "❌ 模型名称不能为空"

    # 检查是否已存在
    if app.global_config.get_model(name):
        return f"❌ 模型 '{name}' 已存在"

    api_key = input("API Key: ").strip()
    if not api_key:
        return "❌ API Key不能为空"

    base_url = input("API Base URL (例如 https://api.openai.com/v1): ").strip()
    if not base_url:
        return "❌ API Base URL不能为空"

    model_id = input("模型ID (例如 gpt-4o): ").strip()
    if not model_id:
        return "❌ 模型ID不能为空"

    context_limit_str = input("上下文长度限制 (默认 128000): ").strip()
    context_limit = 128000
    if context_limit_str:
        try:
            context_limit = int(context_limit_str)
        except ValueError:
            return "❌ 上下文长度必须是数字"

    model_config = {
        "name": name,
        "apiKey": api_key,
        "url": base_url,
        "model": model_id,
        "context_limit": context_limit
    }

    app.global_config.add_model(model_config)
    return f"✅ 模型 '{name}' 已添加\n   模型ID: {model_id}\n   上下文限制: {context_limit:,} tokens"


def _list_models(app):
    """列出所有模型"""
    models = app.global_config.get_models()
    if not models:
        return "📭 暂无模型。使用 /model add 添加模型。"

    current_model = None
    if app.llm_client:
        current_model = app.llm_client.model_name

    lines = ["📋 已配置的模型:\n"]
    for m in models:
        marker = " ◀ 当前使用" if m.get("model") == current_model else ""
        lines.append(f"  • {m['name']}{marker}")
        lines.append(f"    模型ID: {m['model']}")
        lines.append(f"    API: {m['url']}")
        ctx = m.get('context_limit', 128000)
        lines.append(f"    上下文限制: {ctx:,} tokens")
        lines.append("")

    return "\n".join(lines)


def _show_model_menu(app):
    """显示模型交互式选择菜单"""
    models = app.global_config.get_models()
    if not models:
        return "📭 暂无模型。使用 /model add 添加模型。"

    current_model = None
    if app.llm_client:
        current_model = app.llm_client.model_name

    lines = ["📋 选择模型 (输入编号或名称):\n"]
    for i, m in enumerate(models, 1):
        marker = " ◀ 当前" if m.get("model") == current_model else ""
        ctx = m.get('context_limit', 128000)
        lines.append(f"  {i}. {m['name']}{marker}")
        lines.append(f"     模型ID: {m['model']}  上下文: {ctx:,}")
        lines.append("")

    lines.append(f"  0. 取消")
    lines.append("")

    print("\n" + "\n".join(lines))
    choice = input("请选择 [编号/名称]: ").strip()

    if not choice or choice == '0':
        return "已取消选择"

    if choice.isdigit():
        idx = int(choice)
        if idx == 0:
            return "已取消选择"
        if 1 <= idx <= len(models):
            return _use_model(app, models[idx - 1]['name'])
        else:
            return f"❌ 无效编号 (1-{len(models)})"

    for m in models:
        if m['name'].lower() == choice.lower():
            return _use_model(app, m['name'])

    return f"❌ 未找到模型: {choice}"


def _show_rm_model_menu(app):
    """显示模型删除选择菜单"""
    models = app.global_config.get_models()
    if not models:
        return "📭 暂无模型。"

    lines = ["📋 选择要删除的模型 (输入编号或名称):\n"]
    for i, m in enumerate(models, 1):
        lines.append(f"  {i}. {m['name']} ({m['model']})")
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
        if 1 <= idx <= len(models):
            return _delete_model(app, models[idx - 1]['name'])
        else:
            return f"❌ 无效编号 (1-{len(models)})"

    for m in models:
        if m['name'].lower() == choice.lower():
            return _delete_model(app, m['name'])

    return f"❌ 未找到模型: {choice}"


def _use_model(app, model_name):
    """使用指定模型"""
    model_config = app.global_config.get_model(model_name)
    if not model_config:
        return f"❌ 模型 '{model_name}' 不存在"

    # 更新当前Agent的模型
    if app.current_agent_config:
        app.current_agent_config.primary_model = model_name
        app.agent_manager._save_config(app.current_agent_config)

    # 保存上次选择的模型
    app.global_config.set_last_selected_model(model_name)

    # 重新加载Agent以应用新模型
    if app.current_agent_name:
        app._load_agent(app.current_agent_name)

    ctx = model_config.get('context_limit', 128000)
    return f"✅ 已切换到模型: {model_name}\n   模型ID: {model_config['model']}\n   上下文限制: {ctx:,} tokens"


def _delete_model(app, model_name):
    """删除模型"""
    if app.global_config.delete_model(model_name):
        return f"✅ 模型 '{model_name}' 已删除"
    return f"❌ 模型 '{model_name}' 不存在"


def _model_info(app):
    """查看当前模型信息"""
    if not app.llm_client:
        return "⚠️  当前Agent未配置模型"

    lines = [f"📋 当前模型信息:\n"]
    lines.append(f"  名称: {app.current_agent_config.primary_model if app.current_agent_config else '未知'}")
    lines.append(f"  模型ID: {app.llm_client.model_name}")
    lines.append(f"  API Base: {app.llm_client.base_url}")
    lines.append(f"  上下文限制: {app.llm_client.context_limit:,} tokens")

    if app.context_window:
        total = app.session.get_total_tokens() if app.session else 0
        pct = (total / app.context_window.model_limit * 100) if app.context_window.model_limit > 0 else 0
        lines.append(f"  当前上下文使用: {total:,} / {app.context_window.model_limit:,} ({pct:.1f}%)")

    return "\n".join(lines)


# =============================================================================
# 嵌入模型配置
# =============================================================================

def _embedding_model_menu(app, param):
    """嵌入模型配置菜单"""
    param = param.strip()
    
    if not param:
        return (
            "📊 嵌入模型配置:\n"
            "  /model embedding add    - 添加嵌入模型\n"
            "  /model embedding info   - 查看当前嵌入模型\n"
            "  /model embedding rm     - 删除嵌入模型\n"
            "\n提示: 嵌入模型用于向量数据库的语义搜索"
        )
    
    action = param.split()[0].lower()
    
    if action == "add":
        return _add_embedding_model(app)
    elif action == "info":
        return _embedding_model_info(app)
    elif action == "rm":
        return _delete_embedding_model(app)
    else:
        return f"❌ 未知操作: {action}"


def _add_embedding_model(app):
    """添加嵌入模型"""
    print("\n--- 添加嵌入模型 ---")
    name = input("模型名称 (例如 openai-embedding): ").strip()
    if not name:
        return "❌ 模型名称不能为空"
    
    api_key = input("API Key: ").strip()
    if not api_key:
        return "❌ API Key不能为空"
    
    base_url = input("API Base URL (例如 https://api.openai.com/v1): ").strip()
    if not base_url:
        return "❌ API Base URL不能为空"
    
    model_id = input("模型ID (例如 text-embedding-3-small): ").strip()
    if not model_id:
        return "❌ 模型ID不能为空"
    
    model_type = input("模型类型 (openai/custom, 默认 openai): ").strip() or "openai"
    
    config = {
        "name": name,
        "apiKey": api_key,
        "url": base_url,
        "model": model_id,
        "type": model_type
    }
    
    app.global_config.set_embedding_model(config)
    return f"✅ 嵌入模型 '{name}' 已配置\n   模型ID: {model_id}\n   类型: {model_type}"


def _embedding_model_info(app):
    """查看嵌入模型信息"""
    config = app.global_config.get_embedding_model()
    if not config:
        return "⚠️  未配置嵌入模型\n\n将使用 ChromaDB 内置模型 (all-MiniLM-L6-v2)"
    
    lines = ["📊 当前嵌入模型:\n"]
    lines.append(f"  名称: {config.get('name', '未知')}")
    lines.append(f"  模型ID: {config.get('model', '未知')}")
    lines.append(f"  API Base: {config.get('url', '未知')}")
    lines.append(f"  类型: {config.get('type', 'unknown')}")
    
    if app.embedding_client:
        lines.append("  状态: ✅ 已启用")
    else:
        lines.append("  状态: ⚠️  未启用 (需要重启应用)")
    
    return "\n".join(lines)


def _delete_embedding_model(app):
    """删除嵌入模型"""
    config = app.global_config.get_embedding_model()
    if not config:
        return "⚠️  未配置嵌入模型"
    
    name = config.get('name', '未知')
    confirm = input(f"确定要删除嵌入模型 '{name}' 吗? (y/n): ").strip().lower()
    if confirm != 'y':
        return "已取消删除"
    
    app.global_config.delete_embedding_model()
    return f"✅ 嵌入模型 '{name}' 已删除\n\n将使用 ChromaDB 内置模型"


# =============================================================================
# 重排序模型配置
# =============================================================================

def _rerank_model_menu(app, param):
    """重排序模型配置菜单"""
    param = param.strip()
    
    if not param:
        return (
            "🔄 重排序模型配置:\n"
            "  /model rerank add     - 添加重排序模型\n"
            "  /model rerank info    - 查看当前重排序模型\n"
            "  /model rerank rm      - 删除重排序模型\n"
            "\n提示: 重排序用于提高知识库搜索的相关性"
        )
    
    action = param.split()[0].lower()
    
    if action == "add":
        return _add_rerank_model(app)
    elif action == "info":
        return _rerank_model_info(app)
    elif action == "rm":
        return _delete_rerank_model(app)
    else:
        return f"❌ 未知操作: {action}"


def _add_rerank_model(app):
    """添加重排序模型"""
    print("\n--- 添加重排序模型 ---")
    name = input("模型名称 (例如 jina-reranker): ").strip()
    if not name:
        return "❌ 模型名称不能为空"
    
    api_key = input("API Key: ").strip()
    if not api_key:
        return "❌ API Key不能为空"
    
    base_url = input("API Base URL (例如 https://api.jina.ai/v1): ").strip()
    if not base_url:
        return "❌ API Base URL不能为空"
    
    model_id = input("模型ID (例如 jina-reranker-v2-base-multilingual): ").strip()
    if not model_id:
        return "❌ 模型ID不能为空"
    
    top_n = input("返回结果数量 (默认 5): ").strip()
    top_n = int(top_n) if top_n else 5
    
    config = {
        "name": name,
        "apiKey": api_key,
        "url": base_url,
        "model": model_id,
        "top_n": top_n
    }
    
    app.global_config.set_rerank_model(config)
    return f"✅ 重排序模型 '{name}' 已配置\n   模型ID: {model_id}\n   返回数量: {top_n}"


def _rerank_model_info(app):
    """查看重排序模型信息"""
    config = app.global_config.get_rerank_model()
    if not config:
        return "⚠️  未配置重排序模型\n\n知识库搜索将使用向量相似度排序"
    
    lines = ["🔄 当前重排序模型:\n"]
    lines.append(f"  名称: {config.get('name', '未知')}")
    lines.append(f"  模型ID: {config.get('model', '未知')}")
    lines.append(f"  API Base: {config.get('url', '未知')}")
    lines.append(f"  返回数量: {config.get('top_n', 5)}")
    
    if app.rerank_client:
        lines.append("  状态: ✅ 已启用")
    else:
        lines.append("  状态: ⚠️  未启用 (需要重启应用)")
    
    return "\n".join(lines)


def _delete_rerank_model(app):
    """删除重排序模型"""
    config = app.global_config.get_rerank_model()
    if not config:
        return "⚠️  未配置重排序模型"
    
    name = config.get('name', '未知')
    confirm = input(f"确定要删除重排序模型 '{name}' 吗? (y/n): ").strip().lower()
    if confirm != 'y':
        return "已取消删除"
    
    app.global_config.delete_rerank_model()
    return f"✅ 重排序模型 '{name}' 已删除\n\n知识库搜索将使用向量相似度排序"
