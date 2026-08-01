"""索引命令 - 手动触发向量索引"""
from cbhcli_pkg.commands.parser import SlashCommand


def register_embedding_commands(parser, app):
    """注册索引相关命令"""
    
    def embedding_handler(args):
        """处理 /embedding 命令"""
        args = args.strip()
        
        if not args:
            return (
                "📚 向量索引管理\n\n"
                "用法:\n"
                "  /embedding index   - 索引当前 Agent 工作空间\n"
                "  /embedding status  - 查看索引状态\n"
                "  /embedding clear   - 清除当前 Agent 的索引\n"
                "  /embedding reindex - 重新索引（清除后重新索引）\n\n"
                "提示: 启动时不会自动索引，需要手动执行 /embedding index"
            )
        
        if args == "index":
            return _index_workspace(app)
        elif args == "status":
            return _index_status(app)
        elif args == "clear":
            return _index_clear(app)
        elif args == "reindex":
            return _index_reindex(app)
        else:
            return f"未知参数: {args}\n使用 /embedding 查看帮助"
    
    parser.register(SlashCommand(
        name="embedding",
        description="手动触发向量索引（索引工作空间文件到向量数据库）",
        usage="index|status|clear|reindex",
        handler=embedding_handler
    ))


def _index_workspace(app):
    """索引当前 Agent 工作空间"""
    if not app.memory_indexer:
        return "❌ 向量数据库未启用。请先配置嵌入模型: /model embedding add"
    
    if not app.current_agent_name:
        return "❌ 未选择 Agent"
    
    config = app.current_agent_config
    if not config or not config.workspace_path.exists():
        return f"❌ Agent 工作空间不存在: {config.workspace_path if config else '未知'}"
    
    try:
        # 先删除旧集合
        app.vector_store.delete_collection(app.current_agent_name)
        
        # 重新索引
        segments = app.memory_indexer.index_agent_workspace(
            app.current_agent_name, config.workspace_path
        )
        
        if segments > 0:
            app._agent_indexed = True
            return f"✅ 已索引 {segments} 个段落到向量数据库"
        else:
            return "⚠️  未找到可索引的内容"
    except Exception as e:
        return f"❌ 索引失败: {e}"


def _index_status(app):
    """查看索引状态"""
    if not app.vector_store:
        return "❌ 向量数据库未启用"
    
    if not app.current_agent_name:
        return "❌ 未选择 Agent"
    
    try:
        count = app.vector_store.count(app.current_agent_name)
        indexed = "✅ 已索引" if app._agent_indexed else "⚠️  未索引（启动后未执行 /embedding index）"
        
        return (
            f"📊 索引状态\n\n"
            f"当前 Agent: {app.current_agent_name}\n"
            f"索引状态: {indexed}\n"
            f"向量数量: {count}"
        )
    except Exception as e:
        return f"❌ 获取状态失败: {e}"


def _index_clear(app):
    """清除当前 Agent 的索引"""
    if not app.vector_store:
        return "❌ 向量数据库未启用"
    
    if not app.current_agent_name:
        return "❌ 未选择 Agent"
    
    try:
        app.vector_store.delete_collection(app.current_agent_name)
        app._agent_indexed = False
        return f"✅ 已清除 Agent '{app.current_agent_name}' 的索引"
    except Exception as e:
        return f"❌ 清除失败: {e}"


def _index_reindex(app):
    """重新索引"""
    if not app.memory_indexer:
        return "❌ 向量数据库未启用"
    
    # 先清除
    clear_result = _index_clear(app)
    if "❌" in clear_result:
        return clear_result
    
    # 再索引
    return _index_workspace(app)
