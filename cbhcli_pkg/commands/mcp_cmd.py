"""MCP 命令处理"""
from cbhcli_pkg.commands.parser import SlashCommand


def register_mcp_commands(parser, app):
    """注册 MCP 相关命令"""
    
    def mcp_handler(args):
        """MCP 管理命令"""
        if not app.current_agent_name:
            return "❌ 请先选择 Agent"
        
        if not app.mcp_manager:
            return "❌ MCP 管理器未初始化"
        
        args = args.strip()
        if not args:
            return _mcp_help()
        
        # 解析子命令
        parts = args.split(None, 1)
        subcmd = parts[0].lower()
        subargs = parts[1] if len(parts) > 1 else ""
        
        if subcmd == "add":
            return _mcp_add(subargs, app)
        elif subcmd == "list":
            return _mcp_list(app)
        elif subcmd == "rm":
            return _mcp_rm(subargs, app)
        elif subcmd == "refresh":
            return _mcp_refresh(subargs, app)
        elif subcmd == "tools":
            return _mcp_tools(subargs, app)
        elif subcmd == "on":
            return _mcp_toggle(subargs, app, enable=True)
        elif subcmd == "off":
            return _mcp_toggle(subargs, app, enable=False)
        elif subcmd == "help":
            return _mcp_help()
        else:
            return f"❌ 未知子命令: {subcmd}\n\n{_mcp_help()}"
    
    parser.register(SlashCommand(
        name="mcp",
        description="管理 MCP 工具服务器",
        usage="<add|list|rm|refresh|tools|on|off> [参数]",
        handler=mcp_handler,
        requires_agent=True
    ))


def _mcp_help() -> str:
    """显示 MCP 帮助"""
    return """📋 MCP 管理命令

用法: /mcp <子命令> [参数]

子命令:
  add <名称> <URL> [header名=值 ...]  添加 MCP 服务器
  list                                列出所有 MCP 服务器
  rm <名称>                           移除 MCP 服务器
  refresh <名称>                      重新连接并刷新工具
  tools <名称>                        查看服务器的工具列表
  on <服务器> <工具名>                 启用指定工具
  off <服务器> <工具名>                禁用指定工具

示例:
  /mcp add myserver http://localhost:8080/mcp
  /mcp add authed http://localhost:8080/mcp Authorization=Bearer xxx
  /mcp list
  /mcp tools myserver
  /mcp off myserver some_tool
  /mcp on myserver some_tool
  /mcp rm myserver"""


def _mcp_add(args: str, app) -> str:
    """添加 MCP 服务器"""
    if not args.strip():
        return "❌ 用法: /mcp add <名称> <URL> [header名=值 ...]"
    
    parts = args.strip().split()
    if len(parts) < 2:
        return "❌ 用法: /mcp add <名称> <URL> [header名=值 ...]"
    
    name = parts[0]
    url = parts[1]
    
    # 解析 headers
    headers = {}
    for part in parts[2:]:
        if "=" in part:
            key, _, value = part.partition("=")
            headers[key] = value
    
    result = app.mcp_manager.add_server(name, url, headers if headers else None)
    _rebuild_prompt(app)
    return result


def _mcp_list(app) -> str:
    """列出所有 MCP 服务器"""
    servers = app.mcp_manager.list_servers()
    if not servers:
        return "📭 暂无 MCP 服务器\n\n💡 使用 /mcp add <名称> <URL> 添加"
    
    lines = ["📋 MCP 服务器列表：\n"]
    for s in servers:
        status = "✅ 已连接" if s["connected"] else "❌ 未连接"
        lines.append(f"  ● {s['name']} ({status})")
        lines.append(f"    URL: {s['url']}")
        
        if s["tools"]:
            tool_names = [t["name"] for t in s["tools"]]
            lines.append(f"    工具: {', '.join(tool_names)}")
        
        enabled = s.get("enabled_tools")
        if enabled is not None:
            lines.append(f"    已启用: {', '.join(enabled) if enabled else '无'}")
        else:
            lines.append(f"    已启用: 全部")
        
        lines.append("")
    
    return "\n".join(lines)


def _select_server(app, prompt_msg="请选择服务器"):
    """交互式选择 MCP 服务器，返回服务器名称或 None"""
    servers = app.mcp_manager.list_servers()
    if not servers:
        return None

    lines = [f"📋 {prompt_msg} (输入编号或名称):\n"]
    for i, s in enumerate(servers, 1):
        status = "✅" if s["connected"] else "❌"
        tool_count = len(s["tools"]) if s["tools"] else 0
        lines.append(f"  {i}. {s['name']} ({status} {tool_count}个工具)")
    lines.append(f"\n  0. 取消\n")

    print("\n" + "\n".join(lines))
    choice = input("请选择 [编号/名称]: ").strip()

    if not choice or choice == '0':
        return None

    if choice.isdigit():
        idx = int(choice)
        if 1 <= idx <= len(servers):
            return servers[idx - 1]["name"]
        return None

    for s in servers:
        if s["name"].lower() == choice.lower():
            return s["name"]

    return None


def _mcp_rm(args: str, app) -> str:
    """移除 MCP 服务器"""
    name = args.strip()
    if not name:
        name = _select_server(app, "选择要移除的MCP服务器")
        if not name:
            return "已取消" if app.mcp_manager.list_servers() else "📭 暂无 MCP 服务器"
    result = app.mcp_manager.remove_server(name)
    _rebuild_prompt(app)
    return result


def _mcp_refresh(args: str, app) -> str:
    """刷新 MCP 服务器"""
    name = args.strip()
    if not name:
        name = _select_server(app, "选择要刷新的MCP服务器")
        if not name:
            return "已取消" if app.mcp_manager.list_servers() else "📭 暂无 MCP 服务器"
    result = app.mcp_manager.refresh_server(name)
    _rebuild_prompt(app)
    return result


def _mcp_tools(args: str, app) -> str:
    """查看服务器的工具列表"""
    name = args.strip()
    if not name:
        name = _select_server(app, "选择要查看工具的MCP服务器")
        if not name:
            return "已取消" if app.mcp_manager.list_servers() else "📭 暂无 MCP 服务器"
    tools = app.mcp_manager.get_server_tools(name)
    
    if not tools:
        # 检查服务器是否存在
        servers = app.mcp_manager.list_servers()
        server_names = [s["name"] for s in servers]
        if name not in server_names:
            return f"❌ 找不到 MCP 服务器: {name}"
        return f"ℹ️  服务器 '{name}' 暂无工具"
    
    lines = [f"🔧 MCP 服务器 '{name}' 的工具：\n"]
    for t in tools:
        desc = t["description"][:80] if t["description"] else "无描述"
        lines.append(f"  ● {t['name']} ({t['mcp_name']})")
        lines.append(f"    {desc}")
        lines.append("")
    
    return "\n".join(lines)


def _mcp_toggle(args: str, app, enable: bool) -> str:
    """启用/禁用 MCP 工具（支持多选）"""
    parts = args.strip().split()

    if len(parts) == 0:
        # 交互选择服务器
        server_name = _select_server(app, f"选择要{'启用' if enable else '禁用'}工具的MCP服务器")
        if not server_name:
            return "已取消" if app.mcp_manager.list_servers() else "📭 暂无 MCP 服务器"
    else:
        server_name = parts[0]

    if len(parts) < 2:
        # 交互选择工具 - on 显示已禁用的，off 显示已启用的
        all_tools = app.mcp_manager.get_all_server_tools(server_name)
        if not all_tools:
            return f"ℹ️  服务器 '{server_name}' 暂无工具"

        if enable:
            # on: 显示已禁用的工具供选择启用
            candidates = [t for t in all_tools if not t["enabled"]]
            if not candidates:
                return f"ℹ️  服务器 '{server_name}' 的所有工具已启用"
            action_label = "启用"
        else:
            # off: 显示已启用的工具供选择禁用
            candidates = [t for t in all_tools if t["enabled"]]
            if not candidates:
                return f"ℹ️  服务器 '{server_name}' 没有已启用的工具"
            action_label = "禁用"

        lines = [f"📋 选择要{action_label}的工具 (多选用逗号分隔，如: 1,3):\n"]
        for i, t in enumerate(candidates, 1):
            desc = t["description"][:60] if t["description"] else "无描述"
            lines.append(f"  {i}. {t['name']}")
            lines.append(f"     {desc}")
            lines.append("")
        lines.append(f"  0. 取消\n")

        print("\n" + "\n".join(lines))
        choice = input("请选择 [编号/名称]: ").strip()
        if not choice or choice == '0':
            return "已取消"

        # 解析多选
        selected = []
        for part in choice.split(','):
            part = part.strip()
            if part.isdigit():
                idx = int(part)
                if 1 <= idx <= len(candidates):
                    selected.append(candidates[idx - 1]["name"])
            else:
                # 按名称匹配
                for t in candidates:
                    if t["name"] == part:
                        selected.append(t["name"])
                        break

        if not selected:
            return "❌ 未选择任何工具"

        # 去重
        selected = list(dict.fromkeys(selected))

        # 批量执行
        results = []
        for tool_name in selected:
            result = app.mcp_manager.toggle_tool(server_name, tool_name, enable)
            results.append(result)

        _rebuild_prompt(app)
        return "\n".join(results)
    else:
        # 命令行直接指定：支持逗号分隔多个工具名
        tool_names = [t.strip() for t in parts[1].split(',') if t.strip()]
        results = []
        for tool_name in tool_names:
            result = app.mcp_manager.toggle_tool(server_name, tool_name, enable)
            results.append(result)
        _rebuild_prompt(app)
        return "\n".join(results)


def _rebuild_prompt(app):
    """重建系统提示（更新工具描述，保留对话历史）"""
    try:
        app._update_system_prompt()
    except Exception:
        pass
