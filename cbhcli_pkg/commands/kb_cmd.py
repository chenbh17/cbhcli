"""知识库命令处理 - /kb 斜杠命令"""
from cbhcli_pkg.core.prompt_utils import ask_text
from cbhcli_pkg.commands.parser import SlashCommand


def register_kb_commands(parser, app):
    """注册知识库相关命令"""

    # /kb 命令
    def kb_handler(args):
        args = args.strip()

        if not args:
            return (
                "📚 知识库管理命令:\n"
                "  /kb add <file>      - 添加文件到知识库\n"
                "  /kb list            - 列出知识库中的文件\n"
                "  /kb rm <file>       - 从知识库删除文件\n"
                "  /kb reindex         - 重新索引整个知识库\n"
                "  /kb status          - 查看知识库状态"
            )

        parts = args.split(None, 1)
        action = parts[0].lower()
        param = parts[1] if len(parts) > 1 else ""

        if action == "add":
            if not param:
                return "❌ 用法: /kb add <file_path>"
            return _add_file(app, param.strip())

        elif action == "list":
            return _list_files(app)

        elif action == "rm":
            if not param:
                return _show_rm_kb_menu(app)
            return _remove_file(app, param.strip())

        elif action == "reindex":
            return _reindex(app)

        elif action == "status":
            return _status(app)

        else:
            return f"❌ 未知操作: {action}"

    parser.register(SlashCommand(
        name="kb",
        description="管理知识库",
        usage="add|list|rm|reindex|status [file]",
        handler=kb_handler,
        requires_agent=True
    ))


def _add_file(app, file_path):
    """添加文件到知识库"""
    if not app.current_agent_name:
        return "❌ 请先选择 Agent"

    # 导入 KnowledgeBase
    from cbhcli_pkg.core.knowledge_base import KnowledgeBase

    kb = KnowledgeBase(
        agent_name=app.current_agent_name,
        vector_store=app.vector_store,
        indexer=app.memory_indexer
    )

    result = kb.add_file(file_path)

    if result["success"]:
        return f"✅ {result['message']}\n   索引段落数: {result.get('segments', 0)}"
    else:
        return f"❌ {result['message']}"


def _list_files(app):
    """列出知识库文件"""
    if not app.current_agent_name:
        return "❌ 请先选择 Agent"

    from cbhcli_pkg.core.knowledge_base import KnowledgeBase

    kb = KnowledgeBase(
        agent_name=app.current_agent_name,
        vector_store=app.vector_store,
        indexer=app.memory_indexer
    )

    files = kb.list_files()

    if not files:
        return "📚 知识库为空。使用 /kb add <file> 添加文件。"

    lines = ["📚 知识库文件列表:\n"]
    for f in files:
        size_kb = f["size"] / 1024
        lines.append(f"  • {f['name']} ({size_kb:.1f} KB)")

    return "\n".join(lines)


def _show_rm_kb_menu(app):
    """显示知识库文件删除选择菜单"""
    if not app.current_agent_name:
        return "❌ 请先选择 Agent"

    from cbhcli_pkg.core.knowledge_base import KnowledgeBase
    kb = KnowledgeBase(
        agent_name=app.current_agent_name,
        vector_store=app.vector_store,
        indexer=app.memory_indexer
    )
    files = kb.list_files()
    if not files:
        return "📚 知识库为空。"

    lines = ["📋 选择要删除的文件 (输入编号或文件名):\n"]
    for i, f in enumerate(files, 1):
        size_kb = f["size"] / 1024
        lines.append(f"  {i}. {f['name']} ({size_kb:.1f} KB)")
    lines.append(f"\n  0. 取消\n")

    print("\n" + "\n".join(lines))
    choice = ask_text("请选择 [编号/文件名]: ").strip()

    if not choice or choice == '0':
        return "已取消"

    if choice.isdigit():
        idx = int(choice)
        if 1 <= idx <= len(files):
            return _remove_file(app, files[idx - 1]['name'])
        return f"❌ 无效编号 (1-{len(files)})"

    return _remove_file(app, choice)


def _remove_file(app, file_name):
    """从知识库删除文件"""
    if not app.current_agent_name:
        return "❌ 请先选择 Agent"

    from cbhcli_pkg.core.knowledge_base import KnowledgeBase

    kb = KnowledgeBase(
        agent_name=app.current_agent_name,
        vector_store=app.vector_store,
        indexer=app.memory_indexer
    )

    result = kb.remove_file(file_name)

    if result["success"]:
        return f"✅ {result['message']}"
    else:
        return f"❌ {result['message']}"


def _reindex(app):
    """重新索引知识库"""
    if not app.current_agent_name:
        return "❌ 请先选择 Agent"

    from cbhcli_pkg.core.knowledge_base import KnowledgeBase

    kb = KnowledgeBase(
        agent_name=app.current_agent_name,
        vector_store=app.vector_store,
        indexer=app.memory_indexer
    )

    result = kb.reindex_all()

    if result["success"]:
        return f"✅ {result['message']}"
    else:
        return f"❌ {result['message']}"


def _status(app):
    """查看知识库状态"""
    if not app.current_agent_name:
        return "❌ 请先选择 Agent"

    from cbhcli_pkg.core.knowledge_base import KnowledgeBase
    from pathlib import Path

    kb = KnowledgeBase(
        agent_name=app.current_agent_name,
        vector_store=app.vector_store,
        indexer=app.memory_indexer
    )

    files = kb.list_files()
    kb_dir = kb.kb_dir

    lines = [
        f"📚 知识库状态:\n",
        f"  Agent: {app.current_agent_name}",
        f"  目录: {kb_dir}",
        f"  文件数: {len(files)}",
    ]

    if app.vector_store:
        try:
            count = app.vector_store.count(app.current_agent_name)
            lines.append(f"  向量文档数: {count}")
        except Exception:
            lines.append(f"  向量文档数: 未知")
    else:
        lines.append(f"  向量数据库: 未启用")

    if app.memory_indexer:
        lines.append(f"  索引器: 已启用")
    else:
        lines.append(f"  索引器: 未启用")

    return "\n".join(lines)
