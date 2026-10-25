"""登录管理斜杠命令（v5.4.0）：/login /logout /whoami /ping

/login 为向导式登录（地址 -> 连通性测试 -> 密钥 -> 成功确认），
与 shell `cbhcli login` 共享同一套 auth 模块与凭据文件（设备级，四端通行）。
"""
from __future__ import annotations

from cbhcli_pkg.auth import guide
from cbhcli_pkg.auth.client import AuthClient, AuthError, normalize_server
from cbhcli_pkg.auth.login_cmd import perform_login, perform_logout
from cbhcli_pkg.auth.store import (AuthStore, auth_status, load_device_id,
                                   parse_iso)
from cbhcli_pkg.commands.parser import SlashCommand


def register_auth_commands(parser, app):
    """注册登录管理命令"""

    # ===============================================================
    #  /login
    # ===============================================================

    def login_handler(args):
        parts = args.split() if args.strip() else []
        server_arg = parts[0] if parts else ""
        key_arg = parts[1] if len(parts) > 1 else ""

        store = AuthStore()
        cur = store.load()
        out = []

        def flush():
            """提问前先输出已累积内容（交互提示需要即时可见）"""
            nonlocal out
            if out:
                print("\n".join(out))
                out = []

        # 已登录：显示当前身份 + 确认覆盖
        if cur:
            user = cur.get("user") or {}
            name = user.get("display_name") or user.get("name") or "?"
            expired = store.is_expired(cur)
            if not expired and not server_arg:
                out.append(f"ℹ️  当前已登录: {name}（服务器 {cur.get('server', '')}）")
                flush()
                confirm = _ask("   要重新登录吗？（将覆盖当前凭据）[y/N]: ")
                if confirm.lower() not in ("y", "yes"):
                    return "已取消，现有凭据保持不变。"
            elif expired and not server_arg:
                out.append("⚠️  当前凭据已过期。")
                out.append("    如管理员已为您续期，可直接尝试对话或 /ping 验证；"
                           "否则请重新登录或联系管理员。")
                out.append("")

        out.append("🔐 CBHCLI 登录向导")
        out.append("   登录后即可使用 AI 对话与全部功能（CLI / Web / Jupyter / exec）。")
        out.append("")

        # ---- [1/3] 服务器地址 ----
        if not server_arg:
            hint = (cur or {}).get("server", "")
            out.append("  [1/3] 服务器地址")
            out.append("        请输入管理员提供的服务器地址（IP:端口），直接回车取消。")
            out.append("        示例: 192.168.1.100:28888")
            flush()
            server_arg = _ask("  > ", hint)
            if not server_arg:
                return "  已取消登录。"
        server = normalize_server(server_arg)
        if not server:
            out.append(f"  ❌ 无效的服务器地址: {server_arg}")
            return "\n".join(out)

        # ---- [2/3] 连通性测试 ----
        out.append("  [2/3] 连通性测试")
        out.append("        ⏳ 正在测试服务器连通性...")
        try:
            ping_info = AuthClient(server=server).ping(server=server)
            compat = "协议兼容" if ping_info.get("compatible") else "⚠️ 协议可能不兼容"
            out.append(f"        ✅ 服务器可达！延迟 {ping_info.get('latency_ms', 0)}ms，"
                       f"服务版本 {ping_info.get('server_version', '?')}（{compat}）")
        except AuthError as e:
            out.append(f"        ❌ 连通性测试失败: {e.message}")
            out.append(f"        💡 {guide.error_guide(e.code, 'cli')}")
            if e.code in ("unreachable", "timeout"):
                flush()
                confirm = _ask("        输入 skip 仍尝试登录，其他取消: ")
                if confirm.lower() != "skip":
                    return "  已取消登录。"

        # ---- [3/3] 认证密钥 ----
        if not key_arg:
            out.append("  [3/3] 认证密钥")
            out.append("        请输入管理员发放的认证密钥（cbhk_ 开头，输入不回显）")
            flush()
            key_arg = _ask_secret("  > ")
            if not key_arg:
                return "  已取消登录。"

        out.append("        ⏳ 正在验证...")
        try:
            result = perform_login(server, key_arg, surface="cli")
        except AuthError as e:
            out.append(f"  ❌ 登录失败: {e.message}")
            out.append(f"     💡 {guide.error_guide(e.code, 'cli')}")
            return "\n".join(out)

        user = result.get("user") or {}
        name = user.get("display_name") or user.get("name") or "?"
        uname = user.get("name") or ""
        exp_text = guide.format_expiry(result.get("expires_at"))

        out.append("")
        out.append("  ✅ 登录成功！")
        out.append(f"     用户:     {name}"
                   + (f" ({uname})" if uname and uname != name else ""))
        out.append(f"     服务器:   {server}")
        out.append(f"     凭据有效期: {exp_text}")
        out.append("")
        out.append("  💡 提示:")
        out.append("     - 登录已在本机全局生效，CLI / Web / Jupyter / exec 四端均可使用")
        out.append("     - 查看状态: /whoami   退出登录: /logout   连接测试: /ping")
        return "\n".join(out)

    # ===============================================================
    #  /logout
    # ===============================================================

    def logout_handler(args):
        store = AuthStore()
        if not store.load():
            return "ℹ️  本机未登录，无需退出。"
        local_only = "--local-only" in (args or "")
        result = perform_logout(local_only=local_only)
        return ("✅ 已退出登录（本地凭据已删除，"
                + ("" if local_only else "服务端 token 已吊销，")
                + "CLI / Web / Jupyter / exec 四端均需重新登录）")

    # ===============================================================
    #  /whoami
    # ===============================================================

    def whoami_handler(args):
        status = auth_status()
        if not status.get("server"):
            return ("ℹ️  未登录。请使用 /login 登录，或运行 cbhcli login\n"
                    "    登录一次即可使用全部功能（CLI / Web / Jupyter / exec）。")
        user = status.get("user") or {}
        name = user.get("display_name") or user.get("name") or "?"
        uname = user.get("name") or ""
        lines = [f"👤 当前登录: {name}"
                 + (f" ({uname})" if uname and uname != name else ""),
                 f"   服务器:   {status.get('server', '')}"]
        exp = status.get("expires_at")
        lines.append(f"   凭据:     有效期至 {guide.format_expiry(exp)}" if exp
                     else "   凭据:     永久（无限期）")
        lines.append(f"   设备标识: {status.get('device_id', '')}")
        if status.get("expired"):
            lines.append("   ⚠️ 凭据已过期 — 请联系管理员续期（续期后重试即自动恢复），"
                         "或重新 /login")
            return "\n".join(lines)

        # 在线校验（尽力而为）
        try:
            info = AuthClient().ping(server=status.get("server", ""))
            resp = AuthClient().validate(surface="cli")
            from cbhcli_pkg.auth.guard import _apply_renew
            _apply_renew(resp, AuthStore())
            lines.append(f"   在线状态: ✅ 正常（延迟 {info.get('latency_ms', 0)}ms）")
        except AuthError as e:
            if e.code in ("unreachable", "timeout"):
                lines.append("   在线状态: 🟡 服务器暂不可达（本地凭据仍有效，可继续使用）")
            else:
                lines.append(f"   在线状态: ⚠️ 凭据校验失败 — "
                             f"{guide.error_guide(e.code, 'cli')}")
        return "\n".join(lines)

    # ===============================================================
    #  /ping
    # ===============================================================

    def ping_handler(args):
        server_arg = ""
        for a in (args or "").split():
            if not a.startswith("-"):
                server_arg = a
                break
        status = auth_status()
        server = normalize_server(server_arg) if server_arg else status.get("server", "")
        if not server:
            return ("❌ 未指定服务器地址（本机也未登录）。\n"
                    "   用法: /ping 192.168.1.100:28888")

        lines = [f"🌐 测试服务器连通性: {server}"]
        try:
            info = AuthClient(server=server).ping(server=server)
            lines.append(f"   ✅ 服务器可达 （延迟 {info.get('latency_ms', 0)}ms）")
            compat = "兼容 ✓" if info.get("compatible") else "不兼容 ✗（请升级 cbhcli）"
            lines.append(f"   ✅ 服务版本: cbhcli-admin {info.get('server_version', '?')}"
                         f"（protocol {info.get('protocol', '?')}，{compat}）")
            if status.get("server") == server and status.get("logged_in"):
                user = status.get("user") or {}
                name = user.get("display_name") or user.get("name") or "?"
                exp = status.get("expires_at")
                exp_text = guide.format_expiry(exp) if exp else "永久"
                lines.append(f"   ✅ 当前登录: {name}，凭据有效（{exp_text}）")
            elif status.get("expired"):
                lines.append("   ⚠️ 本地凭据已过期 — 请联系管理员续期，或重新 /login")
            else:
                lines.append("   ℹ️  本机未登录（可 /login 完成登录）")
        except AuthError as e:
            lines.append(f"   ❌ 检测失败: {e.message}")
            lines.append("")
            lines.append("   诊断清单:")
            lines.append("     · 连接被拒  → 服务未启动或端口错误（请与管理员确认）")
            lines.append("     · 连接超时  → 网络不通或防火墙拦截（telnet IP 端口 自查）")
            lines.append("     · 协议不匹配 → 客户端/服务端版本不一致（升级 cbhcli）")
            lines.append("     · HTTP 错误 → 服务异常（联系管理员检查服务状态）")
        return "\n".join(lines)

    # ===============================================================
    #  注册
    # ===============================================================

    parser.register(SlashCommand(
        name="login",
        description="登录（向导式，含连通性测试）",
        usage="[IP:端口] [密钥]",
        handler=login_handler,
        requires_agent=False,
    ))
    parser.register(SlashCommand(
        name="logout",
        description="退出登录（吊销凭据，四端失效）",
        usage="[--local-only]",
        handler=logout_handler,
        requires_agent=False,
    ))
    parser.register(SlashCommand(
        name="whoami",
        description="查看当前登录身份与凭据状态",
        usage="",
        handler=whoami_handler,
        requires_agent=False,
    ))
    parser.register(SlashCommand(
        name="ping",
        description="服务器连通性测试",
        usage="[IP:端口]",
        handler=ping_handler,
        requires_agent=False,
    ))


# ===================================================================
#  输入辅助
# ===================================================================

def _ask(prompt: str, default: str = "") -> str:
    try:
        from cbhcli_pkg.core.prompt_utils import ask_text
        return ask_text(prompt, default) or default
    except Exception:
        try:
            return input(prompt).strip() or default
        except (KeyboardInterrupt, EOFError):
            return default


def _ask_secret(prompt: str) -> str:
    """密钥输入（不回显）

    契约 D7-05（Q-02 = A）：全屏 UI 走应用内 password 模态；
    普通终端回退 getpass；非 TTY 回退普通输入。
    """
    try:
        from cbhcli_pkg.core.prompt_utils import ask_secret
        return ask_secret(prompt)
    except Exception:
        return _ask(prompt, "")
