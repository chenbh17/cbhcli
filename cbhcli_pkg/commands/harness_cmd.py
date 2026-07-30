"""Harness 命令：/mode /permissions /hooks /undo

- /mode        权限模式切换（readonly/standard/auto/yolo）
- /permissions 权限规则管理（list/add/rm）
- /hooks       钩子管理（list/reload/test）
- /undo        回滚 write/edit 的文件备份
"""
from cbhcli_pkg.commands.parser import SlashCommand
from cbhcli_pkg.core.permissions import MODES, MODE_META
from cbhcli_pkg.core.prompt_utils import ask_text


def register_harness_commands(parser, app):
    """注册 Harness 相关命令"""

    # ==================================================================
    # /mode — 权限模式切换
    # ==================================================================
    def mode_handler(args):
        if not app.permission_engine:
            return "❌ 权限引擎未初始化"

        engine = app.permission_engine
        arg = args.strip().lower()

        # 无参数或 list：显示当前模式 + 全部模式说明
        if not arg or arg == "list":
            lines = ["🛡️  权限模式（Shift+Tab 循环切换，/mode <模式> 直接切换）\n"]
            for m in MODES:
                meta = MODE_META[m]
                current = " ◀ 当前" if m == engine.mode else ""
                lines.append(f"  {meta['icon']} {m.ljust(10)} {meta['desc']}{current}")
            lines.append("")
            lines.append(f"  默认模式: {engine._default_mode}"
                         f"（/mode default <模式> 修改）")
            lines.append(f"  yolo_keep_deny: {engine.yolo_keep_deny}"
                         f"（True 时 YOLO 模式仍硬阻断红线）")
            return "\n".join(lines)

        # /mode default <模式>：设置默认模式
        if arg.startswith("default"):
            parts = arg.split()
            if len(parts) == 2 and parts[1] in MODES:
                engine.set_default_mode(parts[1])
                return f"✅ 默认权限模式已设置为: {parts[1]}"
            return "❌ 用法: /mode default <readonly|standard|auto|yolo>"

        if arg not in MODES:
            return f"❌ 未知模式: {arg}\n可用: {' / '.join(MODES)}"

        # yolo 二次确认（CLI 直接问）
        if arg == "yolo" and engine.mode != "yolo":
            print("\n\033[41;97m ⚠️  YOLO 模式将无确认执行一切操作"
                  "（含 rm/git push 等），deny 红线降级为警告。 \033[0m")
            confirm = ask_text("确认开启 YOLO 模式? [y/N]: ").strip().lower()
            if confirm not in ("y", "yes"):
                return "已取消"

        old_mode = engine.mode
        app.set_permission_mode(arg)
        meta = MODE_META[arg]
        return f"{meta['icon']} 权限模式: {old_mode} → {arg}\n   {meta['desc']}"

    parser.register(SlashCommand(
        name="mode",
        description="权限模式切换（Shift+Tab 循环切换）",
        usage="[readonly|standard|auto|yolo|list|default <模式>]",
        handler=mode_handler,
        requires_agent=True
    ))

    # ==================================================================
    # /permissions — 权限规则管理
    # ==================================================================
    def permissions_handler(args):
        if not app.permission_engine:
            return "❌ 权限引擎未初始化"

        engine = app.permission_engine
        parts = args.strip().split(None, 2)
        action = parts[0].lower() if parts else "list"

        if action == "list":
            rules = engine.get_user_rules()
            lines = [f"🛡️  权限规则（当前模式: {engine.mode}）\n"]
            lines.append("  用户自定义规则（~/.cbhcli/permissions.json）:")
            for cat in ("deny", "ask", "allow"):
                icon = {"deny": "🚫", "ask": "❓", "allow": "✅"}[cat]
                lines.append(f"  {icon} {cat}:")
                if rules[cat]:
                    for r in rules[cat]:
                        lines.append(f"      {r}")
                else:
                    lines.append("      （无）")
            lines.append("")
            lines.append("  内置规则: deny 红线 14 条 / ask 危险操作 15 条"
                         " / allow 只读命令若干（随模式自动生效）")
            lines.append("  规则语法: 工具名(模式)，如 terminal(git status:*)、"
                         "edit(/project/**)、python(*)")
            return "\n".join(lines)

        if action in ("add", "rm"):
            if len(parts) < 3:
                return (f"❌ 用法: /permissions {action} "
                        f"<allow|ask|deny> <规则>\n"
                        f"示例: /permissions {action} allow terminal(pytest:*)")
            category = parts[1].lower()
            rule = parts[2].strip()
            if category not in ("allow", "ask", "deny"):
                return "❌ 类别必须是 allow / ask / deny"
            if action == "add":
                engine.add_rule(category, rule)
                return f"✅ 已添加 {category} 规则: {rule}"
            else:
                if engine.remove_rule(category, rule):
                    return f"✅ 已删除 {category} 规则: {rule}"
                return f"❌ 规则不存在: {rule}"

        return "❌ 用法: /permissions [list|add|rm]"

    parser.register(SlashCommand(
        name="permissions",
        description="权限规则管理（~/.cbhcli/permissions.json）",
        usage="[list|add <allow|ask|deny> <规则>|rm <allow|ask|deny> <规则>]",
        handler=permissions_handler,
        requires_agent=True
    ))

    # ==================================================================
    # /hooks — 钩子管理
    # ==================================================================
    def hooks_handler(args):
        if not getattr(app, "hook_manager", None):
            return "❌ 钩子管理器未初始化"

        hm = app.hook_manager
        arg = args.strip().lower()

        if arg == "reload":
            hm.reload()
            return "✅ 钩子配置已重新加载"

        if arg.startswith("test"):
            from cbhcli_pkg.core.hooks import EVENTS
            parts = args.strip().split(None, 1)
            if len(parts) < 2:
                return (f"❌ 用法: /hooks test <事件名>\n"
                        f"事件: {', '.join(EVENTS)}")
            event = parts[1].strip()
            # 大小写宽容匹配
            event = next((e for e in EVENTS if e.lower() == event.lower()),
                         event)
            if event not in EVENTS:
                return f"❌ 未知事件: {event}\n事件: {', '.join(EVENTS)}"
            decision = hm.run_simple(
                event, session_id=app.session.id if app.session else "")
            lines = [f"🪝 事件 {event} 执行结果:"]
            if decision.outputs:
                lines.append("  stdout:")
                for o in decision.outputs:
                    lines.append(f"    {o}")
            if decision.warnings:
                lines.append("  warnings:")
                for w in decision.warnings:
                    lines.append(f"    ⚠️ {w}")
            if not decision.outputs and not decision.warnings:
                lines.append("  （无输出，无匹配钩子或钩子静默成功）")
            return "\n".join(lines)

        # 默认 list
        hooks = hm.get_hooks()
        if not hooks:
            return ("🪝 未配置任何钩子\n"
                    "配置文件: ~/.cbhcli/hooks.json（全局）或 "
                    "<agent工作空间>/hooks.json\n"
                    "示例: {\"PreToolUse\": [{\"matcher\": \"terminal\", "
                    "\"command\": \"python3 ~/guard.py\"}]}")
        lines = ["🪝 已配置钩子:\n"]
        for event, entries in hooks.items():
            lines.append(f"  {event}:")
            for e in entries:
                matcher = e.get("matcher", "*")
                lines.append(f"    [{matcher}] {e['command']}")
        lines.append("\n  /hooks reload 重新加载 · /hooks test <事件> 测试")
        return "\n".join(lines)

    parser.register(SlashCommand(
        name="hooks",
        description="钩子管理（PreToolUse/PostToolUse/Stop 等）",
        usage="[list|reload|test <事件名>]",
        handler=hooks_handler,
        requires_agent=True
    ))

    # ==================================================================
    # /undo — 回滚文件备份
    # ==================================================================
    def undo_handler(args):
        if not getattr(app, "checkpoint_manager", None) or \
                not app.checkpoint_manager.available:
            return "❌ 检查点管理器未初始化"

        cm = app.checkpoint_manager
        arg = args.strip()

        if arg == "list":
            backups = cm.list_backups(20)
            if not backups:
                return "📭 暂无可回滚的备份"
            lines = ["🕐 可回滚的文件备份（新→旧）:\n"]
            for b in backups:
                ts = b.get("ts", "")[:19].replace("T", " ")
                tool = b.get("tool", "")
                path = b.get("path", "")
                existed = "修改" if b.get("existed") else "新建"
                lines.append(f"  [{b.get('id')}] {ts} {tool}({existed}) {path}")
            lines.append("\n  /undo 回滚最近一次 · /undo <ID> 回滚指定备份")
            return "\n".join(lines)

        if arg:
            ok, msg = cm.undo_by_id(arg)
        else:
            ok, msg = cm.undo_last()

        return ("✅ " if ok else "❌ ") + msg

    parser.register(SlashCommand(
        name="undo",
        description="回滚 write/edit 的文件修改（自动备份）",
        usage="[<备份ID>|list]",
        handler=undo_handler,
        requires_agent=True
    ))
