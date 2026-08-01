"""会话和上下文命令处理"""
from cbhcli_pkg.core.prompt_utils import ask_text
from cbhcli_pkg.commands.parser import SlashCommand


def register_session_commands(parser, app):
    """注册会话相关命令"""
    
    # /reset 或 /new 命令
    def reset_handler(args):
        """重置或创建新会话，自动保存当前会话"""
        if not app.current_agent_name:
            return "❌ 请先选择Agent: /agent switch <name>"
        
        app._reset_session(save_current=True)
        return "✅ 已创建新会话 (上一会话已保存到 history 文件夹)"
    
    parser.register(SlashCommand(
        name="reset",
        description="重置当前会话（自动保存上一会话）",
        usage="",
        handler=reset_handler,
        requires_agent=True
    ))
    
    parser.register(SlashCommand(
        name="new",
        description="创建新会话（自动保存上一会话）",
        usage="",
        handler=reset_handler,
        requires_agent=True
    ))
    
    # /resume 命令 - 恢复历史会话
    def resume_handler(args):
        """列出或恢复历史会话（交互式选择）"""
        if not app.current_agent_name:
            return "❌ 请先选择Agent"
        
        if not app.session_history:
            return "❌ 会话历史管理器未初始化"
        
        sessions = app.session_history.list_sessions(20)
        if not sessions:
            return "📭 暂无历史会话"
        
        # 如果提供了参数，直接用参数选择
        choice = args.strip()
        
        if not choice:
            # 无参数时显示交互式选择菜单
            lines = ["📋 选择要恢复的会话 (输入编号或文件名):\n"]
            for i, s in enumerate(sessions, 1):
                created = s.get("created_at", "")[:16].replace("T", " ")
                title = s.get("title", "")[:40]
                count = s.get("message_count", 0)
                lines.append(f"  {i:2d}. [{created}] {title} ({count} 条消息)")
            lines.append(f"\n   0. 取消")
            lines.append("")
            
            print("\n" + "\n".join(lines))
            choice = ask_text("请选择 [编号/文件名]: ").strip()
            
            if not choice or choice == '0':
                return "已取消"
        
        # 解析用户选择
        filename = choice
        messages = app.session_history.load_session(filename)
        if messages is None:
            # 尝试用数字索引查找
            try:
                idx = int(filename) - 1
                if 0 <= idx < len(sessions):
                    filename = sessions[idx]["filename"]
                    messages = app.session_history.load_session(filename)
                else:
                    return f"❌ 无效的会话编号: {choice}"
            except ValueError:
                return f"❌ 找不到会话文件: {filename}"
        
        if messages is None:
            return f"❌ 加载会话失败: {filename}"
        
        # 保存当前会话到 history
        if app.session and len(app.session.messages) > 1:
            try:
                ctx_msgs = app.session.get_context_messages()
                app.session_history.save_session(ctx_msgs, app.session.id)
            except Exception:
                pass
        
        # 在当前 session 上原地恢复（不创建新会话）
        # 清空当前消息，保留 system 消息
        app.session.reset()
        app.session.tool_call_count = 0
        
        # 重新加载历史消息（跳过 system 消息，因为 reset 已保留）
        for msg in messages:
            if msg.get("role") != "system":
                content = msg.get("content") or ""
                app.session.add_message(
                    role=msg["role"],
                    content=content,
                    token_count=app.token_counter.count_tokens(content),
                    metadata=msg,
                    tool_call_id=msg.get("tool_call_id"),
                    tool_calls=msg.get("tool_calls"),
                    reasoning_content=msg.get("reasoning_content")
                )
        
        # 统计恢复的消息
        user_count = sum(1 for m in messages if m.get("role") == "user")
        return f"✅ 已恢复会话（{user_count} 轮对话，{len(messages)} 条消息）"
    
    parser.register(SlashCommand(
        name="resume",
        description="列出或恢复历史会话",
        usage="[编号或文件名]",
        handler=resume_handler,
        requires_agent=True
    ))
    
    # /history 命令 - 查看历史会话列表（/resume 的别名）
    def history_handler(args):
        """查看历史会话列表"""
        return resume_handler("")
    
    parser.register(SlashCommand(
        name="history",
        description="查看历史会话列表",
        usage="",
        handler=history_handler,
        requires_agent=True
    ))
    
    # /comp 命令 - 手动压缩上下文（支持带指令：/comp 保留迁移方案，丢弃调试过程）
    def compress_handler(args):
        """手动压缩上下文，可携带保留/丢弃指令"""
        if not app.current_agent_name:
            return "❌ 请先选择Agent"

        if app.session is None:
            return "❌ 当前没有活动会话"

        instructions = args.strip()

        # 执行压缩
        success = app._compress_context(instructions=instructions)

        if success:
            if instructions:
                return f"✅ 上下文已压缩（按指令: {instructions}）"
            return "✅ 上下文已压缩"
        else:
            return "ℹ️  上下文较短,无需压缩"

    parser.register(SlashCommand(
        name="comp",
        description="手动压缩上下文（可带指令: /comp 保留X 丢弃Y）",
        usage="[压缩指令]",
        handler=compress_handler,
        requires_agent=True
    ))

    # /undo-compress 命令 - 撤销最近一次上下文压缩
    def undo_compress_handler(args):
        """撤销最近一次上下文压缩（从备份恢复原始消息）"""
        if not app.current_agent_name:
            return "❌ 请先选择Agent"

        if app.session is None:
            return "❌ 当前没有活动会话"

        if not app.context_compressor:
            return "❌ 压缩组件未初始化"

        backups = app.context_compressor.list_backups()
        if not backups:
            return "📭 没有可撤销的压缩记录"

        choice = args.strip()

        if not choice:
            # 无参数时显示交互式选择菜单
            lines = ["📋 选择要恢复的压缩记录 (输入编号):\n"]
            for i, b in enumerate(backups, 1):
                lines.append(
                    f"  {i:2d}. [{b['time']}] "
                    f"{b['before_tokens']:,} → {b['after_tokens']:,} tokens "
                    f"({b['message_count']} 条消息)")
            lines.append(f"\n   0. 取消")
            lines.append("")

            print("\n" + "\n".join(lines))
            choice = ask_text("请选择 [编号]: ").strip()

            if not choice or choice == '0':
                return "已取消"

        # 解析编号并恢复
        try:
            idx = int(choice) - 1
            if not (0 <= idx < len(backups)):
                return f"❌ 无效的编号: {choice}"
        except ValueError:
            return f"❌ 无效的编号: {choice}"

        backup = backups[idx]
        success = app.context_compressor.restore_backup(backup["file"], app.session)

        if not success:
            return "❌ 恢复失败（备份文件可能已损坏）"

        # 更新上下文窗口
        if app.context_window:
            total = app.session.get_total_tokens(app.token_counter)
            app.context_window.update(total)

        return (f"✅ 已恢复压缩前的上下文 "
                f"({backup['after_tokens']:,} → {backup['before_tokens']:,} tokens, "
                f"{backup['time']})")

    parser.register(SlashCommand(
        name="undo-compress",
        description="撤销最近一次上下文压缩（恢复压缩前的原始消息）",
        usage="[编号]",
        handler=undo_compress_handler,
        requires_agent=True
    ))
    
    # /ctx 命令 - 显示上下文使用情况
    def context_handler(args):
        """显示上下文使用情况"""
        if not app.current_agent_name:
            return "❌ 请先选择Agent"

        if app.context_window is None:
            return "❌ 上下文窗口未初始化"

        # 获取模型名称
        model_name = "未配置"
        if app.llm_client and hasattr(app.llm_client, 'model_name'):
            model_name = app.llm_client.model_name
        elif app.current_agent_config and app.current_agent_config.primary_model:
            model_name = app.current_agent_config.primary_model

        # 更新 context_window
        if app.session:
            total_tokens = app.session.get_total_tokens()
            app.context_window.update(total_tokens)

        status = app.context_window.get_status_text()
        remaining = app.context_window.remaining_tokens()

        lines = [
            f"🤖 Agent: {app.current_agent_name}",
            f"📊 模型: {model_name}",
            f"",
            f"📈 {status}",
            f"📝 剩余: {remaining:,} tokens",
        ]

        # 按消息类型统计 token 分布
        if app.session:
            system_tokens = 0
            user_tokens = 0
            assistant_tokens = 0
            tool_tokens = 0
            for msg in app.session.messages:
                if msg.role == "system":
                    system_tokens += msg.token_count
                elif msg.role == "user":
                    user_tokens += msg.token_count
                elif msg.role == "assistant":
                    assistant_tokens += msg.token_count
                elif msg.role == "tool":
                    tool_tokens += msg.token_count

            lines.append("")
            lines.append("📦 Token 分布:")
            lines.append(f"  系统提示:    {system_tokens:>8,} tokens (含 soul/tools/usage/memory/skills)")
            lines.append(f"  tools schema: {app.context_window.tools_schema_tokens:>7,} tokens (OpenAI function calling 定义)")
            lines.append(f"  用户消息:    {user_tokens:>8,} tokens")
            lines.append(f"  AI 回复:     {assistant_tokens:>8,} tokens")
            lines.append(f"  工具结果:    {tool_tokens:>8,} tokens")

        lines.append("")
        lines.append(f"💬 消息数: {len(app.session.messages) if app.session else 0}")
        lines.append(f"🔧 工具调用: {app.session.tool_call_count if app.session else 0}")
        lines.append("")
        lines.append(f"⚙️  自动压缩: {'启用' if app.current_agent_config and app.current_agent_config.auto_compress else '禁用'}")
        lines.append(f"🎯 压缩阈值: {app.context_window.compression_ratio * 100:.0f}%")

        return "\n".join(lines)
    
    parser.register(SlashCommand(
        name="ctx",
        description="显示上下文使用情况",
        usage="",
        handler=context_handler,
        requires_agent=True
    ))
