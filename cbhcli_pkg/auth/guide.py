"""登录指引文案（全端统一，集中常量防口径漂移）

surface: cli / web / jupyter / exec

注意：CLI 方框提示必须用 `_boxed()` 动态生成（字素簇宽度补齐），
禁止手工空格对齐——中英文/emoji 混排手工空格必然错位。
"""
from __future__ import annotations


def _display_width(s: str) -> int:
    """显示宽度（字素簇感知；退化实现仅作兜底）"""
    try:
        from cbhcli_pkg.core.text_width import display_width
        return display_width(s)
    except Exception:
        # 兜底：CJK/全角/emoji 粗略按 2 计
        w = 0
        for ch in s:
            cp = ord(ch)
            w += 2 if (0x1100 <= cp <= 0x115F or 0x2E80 <= cp <= 0xA4CF
                       or 0xAC00 <= cp <= 0xD7A3 or 0xF900 <= cp <= 0xFAFF
                       or 0xFE30 <= cp <= 0xFE4F or 0xFF00 <= cp <= 0xFF60
                       or 0xFFE0 <= cp <= 0xFFE6 or 0x1F300 <= cp <= 0x1FAFF
                       or 0x20000 <= cp) else 1
        return w


def _boxed(lines: list) -> str:
    """生成右侧严格对齐的方框（按显示宽度动态补齐最后一列空格）

    行格式: `│ <内容><补齐空格> │`；边框长度 = 最宽行 + 2。
    """
    width = max((_display_width(l) for l in lines), default=0)
    top = "╭" + "─" * (width + 2) + "╮"
    bot = "╰" + "─" * (width + 2) + "╯"
    body = []
    for l in lines:
        pad = " " * max(0, width - _display_width(l))
        body.append(f"│ {l}{pad} │")
    return "\n".join([top] + body + [bot])


def format_expiry(expires_at) -> str:
    """格式化有效期显示：None/空 -> 「永久（无限期）」；否则「YYYY-MM-DD HH:MM（剩余 N 天）」。"""
    if not expires_at:
        return "永久（无限期）"
    from cbhcli_pkg.auth.store import parse_iso
    dt = parse_iso(str(expires_at))
    if dt is None:
        return str(expires_at)
    text = dt.strftime("%Y-%m-%d %H:%M")
    from datetime import datetime, timezone
    days = (dt - datetime.now(timezone.utc)).total_seconds() / 86400.0
    if days < 0:
        return f"{text}（已过期）"
    if days < 1:
        return f"{text}（剩余 {max(0, int(days * 24))} 小时）"
    return f"{text}（剩余 {int(days)} 天）"


def not_logged_in_short(surface: str = "cli") -> str:
    """一行式提示（欢迎面板/状态栏/横幅用）。"""
    if surface == "web":
        return "🔐 未登录 — 请在登录界面完成登录"
    if surface == "jupyter":
        return "🔐 本机未登录 cbhcli — 请运行 cbhcli login，或打开 cbhcli web 页面登录"
    if surface == "exec":
        return "❌ 未登录：cbhcli exec 需要先登录。请运行: cbhcli login"
    return "🔐 未登录 — 输入 /login 登录，或运行 cbhcli login"


def logged_in_short(user: dict, server: str = "") -> str:
    """已登录的一行式展示（欢迎面板/状态栏用）。"""
    if not user:
        return ""
    name = user.get("display_name") or user.get("name") or "?"
    uname = user.get("name") or ""
    tag = f"{name} ({uname})" if name != uname and uname else name
    if server:
        return f"👤 {tag} @ {server}"
    return f"👤 {tag}"


def login_hint_text(surface: str = "cli") -> str:
    """未登录拦截提示（CLI 发消息拦截版为基准；其他 surface 返回对应版本）。"""
    if surface == "exec":
        return ("❌ 未登录：cbhcli exec 需要先登录。\n"
                "   请运行: cbhcli login")
    if surface == "web":
        return "🔐 本机未登录 cbhcli，请在登录界面完成登录"
    if surface == "jupyter":
        return ("🔐 本机未登录 cbhcli。请运行 cbhcli login，"
                "或打开 cbhcli web 页面登录")
    return _boxed([
        "● 未登录 — 无法发起对话",
        "",
        "请先登录（向管理员获取【服务器地址 + 认证密钥】）：",
        "",
        "    /login               在本界面内登录",
        "    cbhcli login         或在终端运行",
        "",
        "登录一次即可使用全部功能（CLI / Web / Jupyter / exec）。",
    ])


def expired_hint_text(surface: str = "cli") -> str:
    """token 已过期/被吊销时的拦截文案（区分「可续期恢复」路径）。"""
    tail = ("如管理员已为您续期，直接重试即可自动恢复；"
            "否则请重新登录（/login 或 cbhcli login）或联系管理员。")
    if surface == "web":
        return "凭据已过期：请联系管理员续期（续期后自动恢复），或重新登录"
    if surface == "exec":
        return f"❌ 凭据已过期：{tail}"
    if surface == "jupyter":
        return ("凭据已过期：请联系管理员续期，或重新登录"
                "（cbhcli login / cbhcli web 登录界面）")
    return f"🔐 凭据已过期 — {tail}"


def error_guide(code: str, surface: str = "cli") -> str:
    """错误码 -> 用户指引文案（登录/连通性场景统一）。"""
    mapping = {
        "unreachable": "无法连接服务器：请确认地址与端口，并检查网络/防火墙"
                       "（提示: telnet IP 端口 自查）",
        "timeout": "连接超时：网络不通或防火墙拦截（可尝试 telnet IP 端口 自查）",
        "tls_error": "TLS 握手失败：服务端证书不受信任（自签证书场景需内部 CA 或改用 HTTP）",
        "invalid_key": "认证密钥无效：密钥错误或已被重置，请联系管理员重新生成",
        "disabled": "账号已被管理员禁用，请联系管理员",
        "protocol_mismatch": "协议版本不匹配：客户端版本过旧/过新，请升级 cbhcli",
        "rate_limited": "请求过于频繁，请稍后再试",
        "not_logged_in": "本机未登录，请先登录",
        "http_error": "服务端响应异常，请联系管理员检查服务状态",
        "server_error": "服务端内部错误，请联系管理员",
        "too_large": "上传内容超过服务端限制",
    }
    return mapping.get(code, f"登录失败（{code}），请重试或联系管理员")
