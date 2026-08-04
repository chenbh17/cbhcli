"""模型配置命令处理"""
from cbhcli_pkg.core.prompt_utils import ask_text
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
                "  /model config       - 修改模型参数\n"
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

        elif action == "config":
            return _config_model(app, param)

        elif action == "embedding":
            return _embedding_model_menu(app, param)

        elif action == "rerank":
            return _rerank_model_menu(app, param)

        else:
            return f"❌ 未知操作: {action}"

    parser.register(SlashCommand(
        name="model",
        description="管理模型配置",
        usage="add|list|use|rm|info|config|embedding|rerank [name]",
        handler=model_handler
    ))


def _add_model(app):
    """添加新模型"""
    print("\n--- 添加新模型 ---")
    name = ask_text("模型名称: ").strip()
    if not name:
        return "❌ 模型名称不能为空"

    # 检查是否已存在
    if app.global_config.get_model(name):
        return f"❌ 模型 '{name}' 已存在"

    api_key = ask_text("API Key: ").strip()
    if not api_key:
        return "❌ API Key不能为空"

    base_url = ask_text("API Base URL (例如 https://api.openai.com/v1): ").strip()
    if not base_url:
        return "❌ API Base URL不能为空"

    model_id = ask_text("模型ID (例如 gpt-4o): ").strip()
    if not model_id:
        return "❌ 模型ID不能为空"

    context_limit_str = ask_text("上下文长度限制 (默认 128000): ").strip()
    context_limit = 128000
    if context_limit_str:
        try:
            context_limit = int(context_limit_str)
        except ValueError:
            return "❌ 上下文长度必须是数字"

    temperature_str = ask_text("温度参数 (默认使用全局值0.1，留空跳过): ").strip()
    temperature = None
    if temperature_str:
        try:
            temperature = float(temperature_str)
            if not 0 <= temperature <= 2:
                return "❌ 温度参数范围: 0-2"
        except ValueError:
            return "❌ 温度参数必须是数字"


    vision_str = ask_text("是否支持视觉/图片输入 (y/n, 默认 n): ").strip().lower()
    supports_vision = vision_str == 'y'

    max_tokens_str = ask_text("最大输出token数 (留空用API默认值, 思考模型建议设置如 8192): ").strip()
    max_tokens = None
    if max_tokens_str:
        try:
            max_tokens = int(max_tokens_str)
            if max_tokens <= 0:
                return "❌ max_tokens 必须大于 0"
        except ValueError:
            return "❌ max_tokens 必须是数字"

    thinking_str = ask_text("thinking 参数 (on/off 或 true/false，留空不传): ").strip().lower()
    thinking = None
    if thinking_str:
        if thinking_str in ('on', 'true', '1', 'yes', 'y'):
            thinking = True
        elif thinking_str in ('off', 'false', '0', 'no', 'n'):
            thinking = False
        else:
            return "❌ thinking 参数只能是 on/off 或 true/false"

    # reasoning_effort 参数：thinking=off 时不能配置（DeepSeek 等 API 报 400）
    reasoning_effort = None
    while True:
        reasoning_effort_str = ask_text("reasoning_effort 参数 (如 minimum/low/medium/high/xhigh/max，留空不传): ").strip()
        if not reasoning_effort_str:
            break
        if reasoning_effort_str.lower() == 'none':
            reasoning_effort = None
            break
        if thinking is False:
            print("⚠️ thinking 已关闭(off)，此时不能配置 reasoning_effort（API 会返回 400 错误）")
            print("   请重新输入：留空不传 / 输入 none 清除；如需 reasoning_effort 请将 thinking 改为 on 后重试")
            continue
        reasoning_effort = reasoning_effort_str
        break

    model_config = {
        "name": name,
        "apiKey": api_key,
        "url": base_url,
        "model": model_id,
        "context_limit": context_limit,
        "vision": supports_vision
    }
    if temperature is not None:
        model_config["temperature"] = temperature
    if max_tokens is not None:
        model_config["max_tokens"] = max_tokens
    if thinking is not None:
        model_config["thinking"] = thinking
    if reasoning_effort is not None:
        model_config["reasoning_effort"] = reasoning_effort

    app.global_config.add_model(model_config)
    temp_info = f"   温度: {temperature}" if temperature is not None else "   温度: 使用全局值(0.1)"
    vision_info = "✅ 支持" if supports_vision else "❌ 不支持"
    max_tokens_info = f"   max_tokens: {max_tokens}" if max_tokens is not None else "   max_tokens: 使用API默认值"
    thinking_info = f"   thinking: {thinking}" if thinking is not None else "   thinking: 不传该参数"
    reasoning_info = f"   reasoning_effort: {reasoning_effort}" if reasoning_effort is not None else "   reasoning_effort: 不传该参数"
    return (f"✅ 模型 '{name}' 已添加\n   模型ID: {model_id}\n   上下文限制: {context_limit:,} tokens\n"
            f"{temp_info}\n   视觉: {vision_info}\n{max_tokens_info}\n{thinking_info}\n{reasoning_info}")


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
        temp = m.get('temperature')
        if temp is not None:
            lines.append(f"    温度: {temp}")
        else:
            lines.append(f"    温度: 使用全局值(0.1)")
        vision = m.get('vision', False)
        lines.append(f"    视觉: {'✅ 支持' if vision else '❌ 不支持'}")
        max_tokens = m.get('max_tokens')
        if max_tokens is not None:
            lines.append(f"    max_tokens: {max_tokens}")
        thinking = m.get('thinking')
        if thinking is not None:
            lines.append(f"    thinking: {thinking}")
        reasoning_effort = m.get('reasoning_effort')
        if reasoning_effort is not None:
            lines.append(f"    reasoning_effort: {reasoning_effort}")
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
    choice = ask_text("请选择 [编号/名称]: ").strip()

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
    choice = ask_text("请选择 [编号/名称]: ").strip()

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

    # 原地切换模型，保留当前会话全部内容（不再重建会话）
    app.switch_model(model_config)

    ctx = model_config.get('context_limit', 128000)
    return f"✅ 已切换到模型: {model_name}\n   模型ID: {model_config['model']}\n   上下文限制: {ctx:,} tokens\n   当前会话内容已保留"


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
    temp = app.llm_client.model_temperature
    if temp is not None:
        lines.append(f"  温度: {temp}")
    else:
        lines.append(f"  温度: 使用全局值(0.1)")
    vision = "✅ 支持" if app.llm_client.supports_vision else "❌ 不支持"
    lines.append(f"  视觉: {vision}")
    max_tokens = app.llm_client.max_tokens
    if max_tokens is not None:
        lines.append(f"  max_tokens: {max_tokens}")
    else:
        lines.append(f"  max_tokens: 使用API默认值")
    thinking = app.llm_client.thinking
    if thinking is not None:
        lines.append(f"  thinking: {thinking}")
    else:
        lines.append(f"  thinking: 不传该参数")
    reasoning_effort = app.llm_client.reasoning_effort
    if reasoning_effort is not None:
        lines.append(f"  reasoning_effort: {reasoning_effort}")
    else:
        lines.append(f"  reasoning_effort: 不传该参数")

    if app.context_window:
        total = app.session.get_total_tokens() if app.session else 0
        pct = (total / app.context_window.model_limit * 100) if app.context_window.model_limit > 0 else 0
        lines.append(f"  当前上下文使用: {total:,} / {app.context_window.model_limit:,} ({pct:.1f}%)")

    return "\n".join(lines)


# =============================================================================
# 模型参数配置
# =============================================================================

def _config_model(app, param):
    """修改模型参数"""
    param = param.strip()
    
    # 如果没有指定模型名称，显示选择菜单
    if not param:
        models = app.global_config.get_models()
        if not models:
            return "📭 暂无模型。使用 /model add 添加模型。"
        
        lines = ["📋 选择要配置的模型 (输入编号或名称):\n"]
        for i, m in enumerate(models, 1):
            lines.append(f"  {i}. {m['name']} ({m['model']})")
        
        lines.append(f"\n  0. 取消")
        print("\n" + "\n".join(lines))
        choice = ask_text("请选择 [编号/名称]: ").strip()
        
        if not choice or choice == '0':
            return "已取消"
        
        if choice.isdigit():
            idx = int(choice)
            if idx == 0:
                return "已取消"
            if 1 <= idx <= len(models):
                model_name = models[idx - 1]['name']
            else:
                return f"❌ 无效编号 (1-{len(models)})"
        else:
            model_name = choice
            # 验证模型存在
            if not app.global_config.get_model(model_name):
                return f"❌ 未找到模型: {choice}"
    else:
        model_name = param
        if not app.global_config.get_model(model_name):
            return f"❌ 未找到模型: {model_name}"
    
    # 获取当前配置
    model_config = app.global_config.get_model(model_name)
    
    print(f"\n--- 配置模型: {model_name} ---")
    print("（直接回车跳过，保持当前值）\n")
    
    # 上下文长度
    current_ctx = model_config.get('context_limit', 128000)
    ctx_str = ask_text(f"上下文长度限制 (当前: {current_ctx}): ").strip()
    if ctx_str:
        try:
            new_ctx = int(ctx_str)
            model_config['context_limit'] = new_ctx
        except ValueError:
            return "❌ 上下文长度必须是数字"
    
    # 温度参数
    current_temp = model_config.get('temperature')
    temp_display = str(current_temp) if current_temp is not None else "使用全局值(0.1)"
    temp_str = ask_text(f"温度参数 (当前: {temp_display}): ").strip()
    if temp_str:
        if temp_str.lower() == 'none' or temp_str == '-':
            # 清除模型专属温度，恢复使用全局值
            model_config.pop('temperature', None)
            print("  → 已清除，将使用全局值(0.1)")
        else:
            try:
                new_temp = float(temp_str)
                if not 0 <= new_temp <= 2:
                    return "❌ 温度参数范围: 0-2"
                model_config['temperature'] = new_temp
            except ValueError:
                return "❌ 温度参数必须是数字（输入 none 或 - 可清除）"
    
    # 视觉参数
    current_vision = model_config.get('vision', False)
    vision_display = "y" if current_vision else "n"
    vision_str = ask_text(f"是否支持视觉/图片输入 (当前: {vision_display}): ").strip().lower()
    if vision_str:
        model_config['vision'] = vision_str == 'y'
    
    # max_tokens 参数
    current_max_tokens = model_config.get('max_tokens')
    max_tokens_display = str(current_max_tokens) if current_max_tokens is not None else "使用API默认值"
    max_tokens_str = ask_text(f"最大输出token数 (当前: {max_tokens_display}, 输入 none 清除): ").strip()
    if max_tokens_str:
        if max_tokens_str.lower() == 'none' or max_tokens_str == '-':
            model_config.pop('max_tokens', None)
            print("  -> 已清除，将使用API默认值")
        else:
            try:
                new_max_tokens = int(max_tokens_str)
                if new_max_tokens <= 0:
                    return "❌ max_tokens 必须大于 0"
                model_config['max_tokens'] = new_max_tokens
            except ValueError:
                return "❌ max_tokens 必须是数字（输入 none 可清除）"

    # thinking 参数
    current_thinking = model_config.get('thinking')
    thinking_display = str(current_thinking) if current_thinking is not None else "不传该参数"
    thinking_str = ask_text(f"thinking 参数 (当前: {thinking_display}, on/off 或 true/false, 输入 none 清除): ").strip().lower()
    if thinking_str:
        if thinking_str in ('none', '-'):
            model_config.pop('thinking', None)
            print("  -> 已清除，将不传该参数")
        elif thinking_str in ('on', 'true', '1', 'yes', 'y'):
            model_config['thinking'] = True
        elif thinking_str in ('off', 'false', '0', 'no', 'n'):
            model_config['thinking'] = False
            # thinking 关闭时不能配置 reasoning_effort（DeepSeek 等 API 报 400）
            if model_config.get('reasoning_effort'):
                print("⚠️ thinking 已关闭(off)，不能同时配置 reasoning_effort（API 会返回 400 错误）")
                model_config.pop('reasoning_effort', None)
                print("  -> 已自动清除 reasoning_effort")
        else:
            return "❌ thinking 参数只能是 on/off 或 true/false（输入 none 可清除）"

    # reasoning_effort 参数：thinking=off 时不能配置（DeepSeek 等 API 报 400）
    current_reasoning_effort = model_config.get('reasoning_effort')
    reasoning_display = str(current_reasoning_effort) if current_reasoning_effort is not None else "不传该参数"
    while True:
        reasoning_str = ask_text(f"reasoning_effort 参数 (当前: {reasoning_display}, 如 minimum/low/medium/high/xhigh/max, 输入 none 清除): ").strip()
        if not reasoning_str:
            break
        if reasoning_str.lower() in ('none', '-'):
            model_config.pop('reasoning_effort', None)
            print("  -> 已清除，将不传该参数")
            break
        if model_config.get('thinking') is False:
            print("⚠️ thinking 已关闭(off)，此时不能配置 reasoning_effort（API 会返回 400 错误）")
            print("   请重新输入：留空不传 / 输入 none 清除；如需 reasoning_effort 请先将 thinking 改为 on")
            continue
        model_config['reasoning_effort'] = reasoning_str
        break

    # 保存配置
    # global_config 中 models 是列表，需要找到并替换
    models = app.global_config.get_models()
    for i, m in enumerate(models):
        if m.get('name') == model_name:
            models[i] = model_config
            break
    app.global_config.save()
    
    # 构建结果信息
    lines = [f"✅ 模型 '{model_name}' 配置已更新:"]
    lines.append(f"   上下文限制: {model_config.get('context_limit', 128000):,} tokens")
    temp = model_config.get('temperature')
    if temp is not None:
        lines.append(f"   温度: {temp}")
    else:
        lines.append(f"   温度: 使用全局值(0.1)")
    vision = "✅ 支持" if model_config.get('vision', False) else "❌ 不支持"
    lines.append(f"   视觉: {vision}")
    max_tokens = model_config.get('max_tokens')
    if max_tokens is not None:
        lines.append(f"   max_tokens: {max_tokens}")
    else:
        lines.append(f"   max_tokens: 使用API默认值")
    thinking = model_config.get('thinking')
    if thinking is not None:
        lines.append(f"   thinking: {thinking}")
    else:
        lines.append(f"   thinking: 不传该参数")
    reasoning_effort = model_config.get('reasoning_effort')
    if reasoning_effort is not None:
        lines.append(f"   reasoning_effort: {reasoning_effort}")
    else:
        lines.append(f"   reasoning_effort: 不传该参数")
    
    # 如果当前正在使用这个模型，提示需要重新加载
    if app.llm_client and app.llm_client.model_name == model_config.get('model'):
        lines.append("\n💡 提示: 当前正在使用此模型，重启 cbhcli 后配置生效")
    
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
    name = ask_text("模型名称 (例如 openai-embedding): ").strip()
    if not name:
        return "❌ 模型名称不能为空"
    
    api_key = ask_text("API Key: ").strip()
    if not api_key:
        return "❌ API Key不能为空"
    
    base_url = ask_text("API Base URL (例如 https://api.openai.com/v1): ").strip()
    if not base_url:
        return "❌ API Base URL不能为空"
    
    model_id = ask_text("模型ID (例如 text-embedding-3-small): ").strip()
    if not model_id:
        return "❌ 模型ID不能为空"
    
    model_type = ask_text("模型类型 (openai/custom, 默认 openai): ").strip() or "openai"
    
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
    confirm = ask_text(f"确定要删除嵌入模型 '{name}' 吗? (y/n): ").strip().lower()
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
    name = ask_text("模型名称 (例如 jina-reranker): ").strip()
    if not name:
        return "❌ 模型名称不能为空"
    
    api_key = ask_text("API Key: ").strip()
    if not api_key:
        return "❌ API Key不能为空"
    
    base_url = ask_text("API Base URL (例如 https://api.jina.ai/v1): ").strip()
    if not base_url:
        return "❌ API Base URL不能为空"
    
    model_id = ask_text("模型ID (例如 jina-reranker-v2-base-multilingual): ").strip()
    if not model_id:
        return "❌ 模型ID不能为空"
    
    top_n = ask_text("返回结果数量 (默认 5): ").strip()
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
    confirm = ask_text(f"确定要删除重排序模型 '{name}' 吗? (y/n): ").strip().lower()
    if confirm != 'y':
        return "已取消删除"
    
    app.global_config.delete_rerank_model()
    return f"✅ 重排序模型 '{name}' 已删除\n\n知识库搜索将使用向量相似度排序"
