"""login / logout / whoami / ping 命令实现（cli.py 分发调用）

- 交互式引导（简洁）：地址 -> 连通性测试 -> 密钥 -> 成功确认
- 也支持一次性参数（脚本/部署）：cbhcli login <server> <key>
- 密钥输入支持环境变量 CBHCLI_AUTH_KEY
- 退出码：0=成功 / 1=失败 / 2=用法错误
"""
from __future__ import annotations

import json
import os
import sys

from cbhcli_pkg.auth import guide
from cbhcli_pkg.auth.client import (AuthClient, AuthError, DEFAULT_PORT,
                                    normalize_server)
from cbhcli_pkg.auth.store import (AuthStore, auth_status, load_device_id,
                                   parse_iso)

PURPLE = "\033[95m"
CYAN = "\033[96m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
RED = "\033[91m"
DIM = "\033[2m"
RESET = "\033[0m"


def _stdin_ready(timeout: float = 0.0) -> bool:
    """stdin 是否有可读数据（非 TTY 场景避免交互阻塞）。"""
    try:
        import select
        return bool(select.select([sys.stdin], [], [], timeout)[0])
    except Exception:
        return False


def _ask(prompt: str, default: str = "") -> str:
    """普通文本输入（键盘中断/非交互无数据时返回默认值，不阻塞）。"""
    if not sys.stdin.isatty() and not _stdin_ready():
        return default
    try:
        text = input(prompt).strip()
        return text or default
    except (KeyboardInterrupt, EOFError):
        return default


def _ask_secret(prompt: str) -> str:
    """密钥输入（不回显；非 TTY 回退普通输入，无数据时返回空不阻塞）。"""
    if not sys.stdin.isatty() and not _stdin_ready():
        return ""
    try:
        import getpass
        if sys.stdin.isatty():
            return getpass.getpass(prompt).strip()
    except Exception:
        pass
    try:
        return input(prompt).strip()
    except (KeyboardInterrupt, EOFError):
        return ""


def _ping_summary(info: dict) -> str:
    latency = info.get("latency_ms", 0)
    ver = info.get("server_version", "?")
    compat = "兼容 ✓" if info.get("compatible") else "不兼容 ✗"
    return f"✅ 服务器可达（延迟 {latency}ms，版本 {ver}，协议 {info.get('protocol', '?')}）"


def _print_error(code: str, surface: str = "cli", extra: str = "") -> None:
    print(f"{RED}✗ {guide.error_guide(code, surface)}{RESET}")
    if extra:
        print(f"{DIM}  {extra}{RESET}")


# ===================================================================
#  无输出登录/登出（Web 端点 / Jupyter 等程序化调用）
# ===================================================================

def perform_login(server: str, key: str, surface: str = "web") -> dict:
    """程序化登录：调管理服务器登录 -> 写 auth.json -> 启动上报。

    Returns:
        {"ok": True, "user": {...}, "server": ..., "expires_at": ...,
         "key_prefix": ...}
    失败抛 AuthError（调用方转成 HTTP 错误响应）。
    """
    server = normalize_server(server)
    if not server:
        raise AuthError("unreachable", "无效的服务器地址")
    key = (key or "").strip()
    if not key:
        raise AuthError("invalid_key", "认证密钥不能为空")

    client = AuthClient(server=server)
    resp = client.login(server, key, surface=surface)
    store = AuthStore()
    if not store.update_after_login(server, resp):
        raise AuthError("server_error", "凭据写入失败（请检查 ~/.cbhcli 目录权限）")

    try:
        from cbhcli_pkg.auth.reporter import get_reporter
        rep = get_reporter()
        rep.start(surface)
        rep.report_event("auth.login", server=server,
                         key_prefix=resp.get("key_prefix", ""),
                         hostname=_hostname(), os_user=_os_user(),
                         surface=surface)
    except Exception:
        pass

    return {
        "ok": True,
        "server": server,
        "user": resp.get("user") or {},
        "expires_at": resp.get("expires_at"),
        "key_prefix": resp.get("key_prefix", ""),
        "device_id": load_device_id(),
    }


def perform_logout(local_only: bool = False) -> dict:
    """程序化登出：吊销服务端 token（尽力而为）+ 删除本地凭据。"""
    store = AuthStore()
    if not store.load():
        return {"ok": True, "message": "本机未登录"}
    if not local_only:
        try:
            AuthClient().logout(store=store)
        except Exception:
            pass
    try:
        from cbhcli_pkg.auth.reporter import get_reporter
        rep = get_reporter()
        rep.report_event("auth.logout")
        rep.stop()
    except Exception:
        pass
    store.clear()
    return {"ok": True, "message": "已退出登录"}


# ===================================================================
#  login
# ===================================================================

def run_login(args: list) -> int:
    """cbhcli login [server] [key] [--server X] [--key Y] [--force]"""
    server_arg = ""
    key_arg = ""
    force = False
    i = 0
    while i < len(args):
        a = args[i]
        if a in ("--server", "-s") and i + 1 < len(args):
            server_arg = args[i + 1]
            i += 2
        elif a in ("--key", "-k") and i + 1 < len(args):
            key_arg = args[i + 1]
            i += 2
        elif a in ("--force", "-f"):
            force = True   # 已登录时直接覆盖（脚本/部署场景）
            i += 1
        elif a in ("-h", "--help"):
            print("用法: cbhcli login [服务器地址] [认证密钥] [--force]")
            print("      cbhcli login --server 192.168.1.100:28888 --key cbhk_xxx")
            print("\n服务器地址: IP（默认补 :28888）/ IP:PORT / http(s)://IP:PORT")
            print("认证密钥: 管理员创建用户时生成（cbhk_ 开头）")
            print("也可用环境变量: CBHCLI_AUTH_KEY")
            print("--force: 已登录时跳过覆盖确认（脚本/部署场景）")
            return 0
        elif not server_arg and not a.startswith("-"):
            server_arg = a
            i += 1
        elif not key_arg and not a.startswith("-"):
            key_arg = a
            i += 1
        else:
            print(f"未识别的参数: {a}")
            return 2

    key_arg = key_arg or os.environ.get("CBHCLI_AUTH_KEY", "").strip()
    store = AuthStore()

    # 已登录时确认覆盖（--force 跳过）
    cur = store.load()
    if cur and not force:
        user = cur.get("user") or {}
        name = user.get("display_name") or user.get("name") or "?"
        print(f"{YELLOW}ℹ️  已登录为 {name}（服务器 {cur.get('server', '')}）{RESET}")
        ans = _ask("   要重新登录吗？（将覆盖当前凭据）[y/N]: ")
        if ans.lower() not in ("y", "yes"):
            print("已取消，现有凭据保持不变。")
            return 0

    print(f"{PURPLE}CBHCLI 用户登录{RESET}")

    # ---- [1/3] 服务器地址 ----
    if not server_arg:
        hint = cur.get("server", "") if cur else ""
        prompt = "  🌐 服务器地址 (IP:端口)"
        if hint:
            prompt += f" [{hint}]"
        prompt += ": "
        server_raw = _ask(prompt, hint)
        if not server_raw:
            print("已取消登录。")
            return 1
    else:
        server_raw = server_arg
    server = normalize_server(server_raw)
    if not server:
        print(f"{RED}✗ 无效的服务器地址: {server_raw}{RESET}")
        return 2
    print(f"  🌐 服务器地址: {server}")

    # ---- [2/3] 连通性测试 ----
    print("  ⏳ 正在测试服务器连通性...", end="", flush=True)
    client = AuthClient(server=server)
    ping_ok = False
    try:
        info = client.ping(server=server)
        print(f"\r  {_ping_summary(info)}")
        ping_ok = True
        if not info.get("compatible"):
            print(f"{YELLOW}     ⚠️ 版本可能不兼容（客户端协议 1，服务端 "
                  f"{info.get('protocol', '?')}），继续尝试登录...{RESET}")
    except AuthError as e:
        print("\r  ❌ 连通性测试失败")
        _print_error(e.code, "cli", e.detail)
        ans = _ask("    仍要尝试登录吗？[y/N]: ")
        if ans.lower() not in ("y", "yes"):
            print("已取消登录。")
            return 1

    # ---- [3/3] 认证密钥 ----
    if not key_arg:
        key = _ask_secret("  🔑 认证密钥（输入不回显）: ")
        if not key:
            print("已取消登录。")
            return 1
    else:
        key = key_arg
    print("  ⏳ 正在验证...")
    try:
        resp = client.login(server, key, surface="cli")
    except AuthError as e:
        print(f"  {RED}✗ 登录失败{RESET}")
        _print_error(e.code, "cli", e.detail)
        return 1

    if not store.update_after_login(server, resp):
        print(f"{RED}✗ 凭据写入失败（请检查 ~/.cbhcli 目录权限）{RESET}")
        return 1

    # 登录成功：启动上报 + 记录 login 事件
    try:
        from cbhcli_pkg.auth.reporter import get_reporter
        rep = get_reporter()
        rep.start("cli")
        rep.report_event("auth.login", server=server,
                         key_prefix=resp.get("key_prefix", ""),
                         hostname=_hostname(), os_user=_os_user())
    except Exception:
        pass

    user = resp.get("user") or {}
    name = user.get("display_name") or user.get("name") or "?"
    uname = user.get("name") or ""
    exp_text = guide.format_expiry(resp.get("expires_at"))
    dev = load_device_id()

    print(f"\n  {GREEN}✅ 登录成功！{RESET}")
    print(f"     用户:     {name}" + (f" ({uname})" if uname and uname != name else ""))
    print(f"     服务器:   {server}")
    print(f"     凭据有效期至: {exp_text}")
    print(f"     设备标识: {dev}（本机）")
    print()
    print("  现在可以使用 cbhcli 全部功能（CLI / Web / Jupyter / exec）。")
    return 0


def _hostname() -> str:
    try:
        import socket
        return socket.gethostname()
    except Exception:
        return ""


def _os_user() -> str:
    try:
        import getpass
        return getpass.getuser()
    except Exception:
        return ""


# ===================================================================
#  logout
# ===================================================================

def run_logout(args: list) -> int:
    """cbhcli logout [--local-only]"""
    local_only = "--local-only" in args
    store = AuthStore()
    cur = store.load()
    if not cur:
        print("ℹ️  本机未登录，无需退出。")
        return 0

    if not local_only:
        try:
            AuthClient().logout(store=store)
        except Exception:
            pass

    # 停止上报 + 记录 logout 事件（尽力而为）
    try:
        from cbhcli_pkg.auth.reporter import get_reporter
        rep = get_reporter()
        rep.report_event("auth.logout")
        rep.stop()
    except Exception:
        pass

    store.clear()
    print(f"{GREEN}✅ 已退出登录{RESET}（本地凭据已删除"
          + ("，" if local_only else "，服务端 token 已吊销，")
          + "CLI / Web / Jupyter / exec 四端均需重新登录）")
    return 0


# ===================================================================
#  whoami
# ===================================================================

def run_whoami(args: list) -> int:
    """cbhcli whoami"""
    status = auth_status()
    if not status.get("logged_in") and not status.get("server"):
        print("ℹ️  未登录。请运行 cbhcli login，或进入 cbhcli 后输入 /login")
        return 0

    user = status.get("user") or {}
    name = user.get("display_name") or user.get("name") or "?"
    uname = user.get("name") or ""
    print(f"👤 当前登录: {name}" + (f" ({uname})" if uname and uname != name else ""))
    print(f"   服务器:   {status.get('server', '')}")
    exp = status.get("expires_at")
    if exp:
        print(f"   凭据:     有效期至 {guide.format_expiry(exp)}")
    else:
        print("   凭据:     永久（无限期）")
    print(f"   设备标识: {status.get('device_id', '')}")

    if status.get("expired"):
        print(f"   {RED}在线状态: ⚠️ 凭据已过期 — 请联系管理员续期，或 cbhcli login{RESET}")
        return 0

    # 在线快速校验（3s 超时；失败如实显示）
    print("   在线状态: ", end="", flush=True)
    try:
        info = AuthClient().ping(server=status.get("server", ""))
        try:
            resp = AuthClient().validate(surface="cli")
            from cbhcli_pkg.auth.guard import _apply_renew
            _apply_renew(resp, AuthStore())
            print(f"{GREEN}✅ 正常{RESET}（服务器可达，延迟 {info.get('latency_ms', 0)}ms）")
        except AuthError as e:
            print(f"{YELLOW}⚠️ 服务器可达但凭据校验失败: "
                  f"{guide.error_guide(e.code, 'cli')}{RESET}")
    except AuthError as e:
        print(f"{YELLOW}⚠️ {e.message} — 本地凭据仍有效，可离线使用{RESET}")
    return 0


# ===================================================================
#  ping
# ===================================================================

def run_ping(args: list) -> int:
    """cbhcli ping [服务器] [--json]"""
    as_json = "--json" in args
    server_arg = ""
    for a in args:
        if a in ("--json", "-h", "--help"):
            continue
        if not a.startswith("-") and not server_arg:
            server_arg = a
    if "-h" in args or "--help" in args:
        print("用法: cbhcli ping [服务器地址] [--json]")
        print("  不带参数: 测试当前登录的服务器（未登录时需指定地址）")
        print("  服务器地址: IP（默认补 :28888）/ IP:PORT / http(s)://IP:PORT")
        return 0

    store = AuthStore()
    status = auth_status()
    server = normalize_server(server_arg) if server_arg else status.get("server", "")
    if not server:
        print("未指定服务器地址（本机也未登录）。用法: cbhcli ping 192.168.1.100:28888")
        return 2

    result = {"server": server, "reachable": False, "latency_ms": None,
              "server_version": "", "protocol": 0, "compatible": False,
              "logged_in": bool(status.get("logged_in")),
              "user": status.get("user") or {},
              "expires_at": status.get("expires_at"),
              "error": ""}

    if not as_json:
        print(f"🌐 测试服务器连通性: {server}")
    try:
        info = AuthClient(server=server).ping(server=server)
        result.update({
            "reachable": True,
            "latency_ms": info.get("latency_ms"),
            "server_version": info.get("server_version", ""),
            "protocol": info.get("protocol", 0),
            "compatible": bool(info.get("compatible")),
        })
        if not as_json:
            print(f"   ✅ 服务器可达 （延迟 {info.get('latency_ms')}ms）")
            compat = "兼容 ✓" if info.get("compatible") else "不兼容 ✗（请升级 cbhcli）"
            print(f"   ✅ 服务版本: cbhcli-admin {info.get('server_version', '?')}"
                  f"（protocol {info.get('protocol', '?')}，{compat}）")
            if result["logged_in"]:
                user = status.get("user") or {}
                name = user.get("display_name") or user.get("name") or "?"
                exp = status.get("expires_at")
                if status.get("expired"):
                    print(f"   {RED}❌ 当前登录: {name} — 凭据已过期，"
                          f"请联系管理员续期或重新登录{RESET}")
                else:
                    exp_text = guide.format_expiry(exp) if exp else "永久"
                    print(f"   ✅ 当前登录: {name}，凭据有效（{exp_text}）")
            else:
                print("   ℹ️  本机未登录（登录后可额外汇总显示身份）")
    except AuthError as e:
        result["error"] = e.code
        if as_json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print(f"   ❌ 检测失败: {e.message}")
            print(f"\n{DIM}诊断清单:{RESET}")
            print(f"{DIM}   · 连接被拒  → 服务未启动或端口错误（请与管理员确认）{RESET}")
            print(f"{DIM}   · 连接超时  → 网络不通或防火墙拦截（telnet IP 端口 自查）{RESET}")
            print(f"{DIM}   · 协议不匹配 → 客户端/服务端版本不一致（升级 cbhcli）{RESET}")
            print(f"{DIM}   · HTTP 错误 → 服务异常（联系管理员检查服务状态）{RESET}")
        return 1

    if as_json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0
