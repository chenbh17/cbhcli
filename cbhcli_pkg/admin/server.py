"""cbhcli-admin 服务入口：FastAPI 应用构建 + run_admin() 启动（含启动密码门）

启动流程：
  cbhcli admin [--port 28888] [--host 0.0.0.0] [--data-dir PATH]
               [--tls-cert CERT --tls-key KEY] [--set-password] [--status]
  → 输入管理员主密码（env CBHCLI_ADMIN_MASTER_KEY 可跳过）→ 验证通过
  → 写 admin.json（salt+hash）→ 监听端口 → 打印管理页面/客户端接入指引

独立入口：cbhcli-admin（等价 cbhcli admin）；cbhcli-admin status 自检。
"""
from __future__ import annotations

import atexit
import json
import os
import socket
import sys
import time
import urllib.request
from pathlib import Path

from fastapi import FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from cbhcli_pkg.admin import data_dir, set_data_dir
from cbhcli_pkg.admin import admin_api, auth_api, report_api
from cbhcli_pkg.admin.master import (master_key_source, store_master_key,
                                     verify_master_key)
from cbhcli_pkg.admin.store import get_store

try:
    from cbhcli_pkg import __version__ as SERVER_VERSION
except Exception:  # pragma: no cover
    SERVER_VERSION = "0.0.0"

PROTOCOL = 1
DEFAULT_PORT = 28888
DEFAULT_HOST = "0.0.0.0"
MAX_PASSWORD_TRIES = 5

STATIC_DIR = Path(__file__).parent / "static"
_SERVER_JSON = "server.json"


# ===================================================================
#  server.json（运行信息，status 自检用）
# ===================================================================

def _server_json_path() -> Path:
    return data_dir() / _SERVER_JSON


def _write_server_json(port: int) -> None:
    try:
        p = _server_json_path()
        p.write_text(json.dumps({
            "pid": os.getpid(),
            "port": port,
            "ts": time.time(),
            "version": SERVER_VERSION,
        }, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def _clear_server_json() -> None:
    try:
        p = _server_json_path()
        if p.exists():
            p.unlink()
    except Exception:
        pass


# ===================================================================
#  应用构建
# ===================================================================

def build_app() -> FastAPI:
    app = FastAPI(title="CBHCLI Admin", version=SERVER_VERSION)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # API 路由（先注册，保证 /admin/api/* 优先于静态挂载）
    app.include_router(auth_api.router)
    app.include_router(report_api.router)
    app.include_router(admin_api.router_open)
    app.include_router(admin_api.router)

    @app.get("/admin")
    async def admin_page():
        return _render_index()

    @app.get("/admin/")
    async def admin_page_slash():
        return _render_index()

    @app.websocket("/admin/ws")
    async def admin_ws(websocket: WebSocket, token: str = ""):
        """管理页面实时事件推送（认证：?token=<admin_token>）。

        客户端上报的事件经 /api/report/batch 到达后实时广播到本连接，
        供管理页面渲染"和 Web 一致的实时交互画面"。
        注意：必须注册在 StaticFiles 挂载（/admin）之前，否则被静态
        路由抢先匹配。
        """
        if not admin_api.check_admin_token(token):
            await websocket.close(code=4401)
            return
        from cbhcli_pkg.admin import ws as ws_hub
        await ws_hub.get_hub().serve(websocket)

    @app.get("/")
    async def root():
        from fastapi.responses import RedirectResponse
        return RedirectResponse(url="/admin")

    # 静态资源挂载（目录不存在时先创建，避免"文件后置创建 -> 404"）
    try:
        STATIC_DIR.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    if STATIC_DIR.exists():
        app.mount("/admin", StaticFiles(directory=str(STATIC_DIR), html=True),
                  name="admin-static")
    return app


def _render_index() -> HTMLResponse:
    try:
        html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
        html = html.replace("__V__", SERVER_VERSION)
        return HTMLResponse(html)
    except Exception as e:
        return HTMLResponse(f"<h1>管理页面缺失</h1><p>{e}</p>", status_code=500)


# ===================================================================
#  LAN IP 展示
# ===================================================================

def _get_lan_ips() -> list:
    """本机内网 IPv4 列表（纯标准库；与 web/server.py 同款实现）"""
    ips: set = set()
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            if not ip.startswith("127."):
                ips.add(ip)
    except Exception:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None,
                                       socket.AF_INET):
            ip = info[4][0]
            if not ip.startswith("127."):
                ips.add(ip)
    except Exception:
        pass

    def _key(ip: str):
        if ip.startswith("192.168."):
            return (0, ip)
        if ip.startswith("10."):
            return (1, ip)
        if ip.startswith("172."):
            return (2, ip)
        return (3, ip)

    return sorted(ips, key=_key)


# ===================================================================
#  启动密码门
# ===================================================================

def _prompt_master_password() -> str:
    """交互式读取主密码（getpass，不回显）。非 TTY 且无 env 时返回空。"""
    env = os.environ.get("CBHCLI_ADMIN_MASTER_KEY", "").strip()
    if env:
        print(f"🔑 使用环境变量 CBHCLI_ADMIN_MASTER_KEY 校验主密码（{master_key_source()}）")
        return env
    if not sys.stdin.isatty():
        return ""
    try:
        import getpass
        return getpass.getpass("🔑 请输入管理员主密码: ").strip()
    except (KeyboardInterrupt, EOFError):
        return ""


def _pass_gate() -> bool:
    """启动门：连续错误 5 次退出。

    Returns: True=通过（并把 salt+hash 写入 admin.json）
    """
    env_key = os.environ.get("CBHCLI_ADMIN_MASTER_KEY", "").strip()
    if env_key:
        if verify_master_key(env_key):
            if not os.path.exists(str(data_dir() / "admin.json")):
                store_master_key(env_key)
            return True
        print("❌ 环境变量 CBHCLI_ADMIN_MASTER_KEY 与已存储主密码不符，拒绝启动。")
        return False

    if not sys.stdin.isatty():
        print("❌ 非交互环境启动需要设置环境变量 CBHCLI_ADMIN_MASTER_KEY。")
        print("   示例: CBHCLI_ADMIN_MASTER_KEY=****** cbhcli admin --port 28888")
        return False

    import getpass
    for i in range(MAX_PASSWORD_TRIES):
        try:
            pwd = getpass.getpass("🔑 请输入管理员主密码: ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\n已取消。")
            return False
        if verify_master_key(pwd):
            store_master_key(pwd)     # 通过后落盘 salt+hash（后续修改主密码）
            return True
        left = MAX_PASSWORD_TRIES - i - 1
        if left > 0:
            print(f"❌ 主密码错误（剩余 {left} 次机会）")
            time.sleep(1)
    print("❌ 连续错误次数过多，拒绝启动。")
    return False


# ===================================================================
#  run_admin
# ===================================================================

def _parse_args(args: list) -> dict:
    opts = {
        "port": DEFAULT_PORT, "host": DEFAULT_HOST, "data_dir": None,
        "tls_cert": None, "tls_key": None, "set_password": False,
        "status": False, "help": False,
    }
    i = 0
    while i < len(args):
        a = args[i]
        if a in ("--port", "-p") and i + 1 < len(args):
            opts["port"] = int(args[i + 1]); i += 2
        elif a == "--host" and i + 1 < len(args):
            opts["host"] = args[i + 1]; i += 2
        elif a == "--data-dir" and i + 1 < len(args):
            opts["data_dir"] = args[i + 1]; i += 2
        elif a == "--tls-cert" and i + 1 < len(args):
            opts["tls_cert"] = args[i + 1]; i += 2
        elif a == "--tls-key" and i + 1 < len(args):
            opts["tls_key"] = args[i + 1]; i += 2
        elif a == "--set-password":
            opts["set_password"] = True; i += 1
        elif a in ("status", "--status"):
            opts["status"] = True; i += 1
        elif a in ("-h", "--help", "help"):
            opts["help"] = True; i += 1
        else:
            print(f"未识别的参数: {a}")
            opts["help"] = True
            i += 1
    return opts


def _print_help() -> int:
    print("CBHCLI Admin — 内网用户认证与审计管理服务")
    print("\n用法: cbhcli admin [选项]")
    print("      cbhcli-admin [选项]        （等价入口）")
    print("      cbhcli admin status        （服务自检）")
    print("\n选项:")
    print(f"  -p, --port PORT      管理服务端口 (默认: {DEFAULT_PORT})")
    print(f"  --host HOST          监听地址 (默认: {DEFAULT_HOST})")
    print("  --data-dir PATH      数据目录 (默认: ~/.cbhcli-admin)")
    print("  --tls-cert CERT      HTTPS 证书文件（可选）")
    print("  --tls-key KEY        HTTPS 私钥文件（可选）")
    print("  --set-password       交互式修改管理员主密码")
    print("  status               服务自检（端口/版本/运行时长/最近上报）")
    print("\n启动门: 输入管理员主密码（内置默认，可用环境变量")
    print("        CBHCLI_ADMIN_MASTER_KEY 覆盖；连续错误 5 次退出）")
    return 0


def _run_set_password() -> int:
    import getpass
    try:
        old = getpass.getpass("当前主密码: ").strip()
    except (KeyboardInterrupt, EOFError):
        print("已取消。")
        return 1
    if not verify_master_key(old):
        print("❌ 当前主密码错误。")
        return 1
    try:
        new1 = getpass.getpass("新主密码: ").strip()
        new2 = getpass.getpass("确认新主密码: ").strip()
    except (KeyboardInterrupt, EOFError):
        print("已取消。")
        return 1
    if not new1 or new1 != new2:
        print("❌ 两次输入不一致或为空。")
        return 1
    if store_master_key(new1):
        print("✅ 主密码已更新（写入 admin.json）。")
        return 0
    print("❌ 写入失败。")
    return 1


def _double_box(lines: list) -> str:
    """双线框 banner（按显示宽度动态补齐，右侧严格对齐）。

    注意：框内不要用 emoji（部分终端 emoji 渲染宽度与 wcwidth 不一致）。
    """
    try:
        from cbhcli_pkg.core.text_width import display_width
    except Exception:
        display_width = len  # type: ignore[assignment]
    w = max((display_width(l) for l in lines), default=0)
    top = "  ╔" + "═" * (w + 2) + "╗"
    bot = "  ╚" + "═" * (w + 2) + "╝"
    body = [f"  ║ {l}{' ' * max(0, w - display_width(l))} ║" for l in lines]
    return "\n".join([top] + body + [bot])


def run_admin(args: list) -> int:
    """cbhcli admin / cbhcli-admin 主入口。"""
    opts = _parse_args(list(args or []))
    if opts["data_dir"]:
        set_data_dir(opts["data_dir"])
    if opts["help"]:
        return _print_help()
    if opts["status"]:
        return run_admin_status()
    if opts["set_password"]:
        return _run_set_password()

    # ---- 启动门：主密码 ----
    print(_double_box(["CBHCLI Admin — 内网用户认证与审计管理服务"]))
    if not _pass_gate():
        return 2
    print("  ✅ 验证通过")

    # ---- 初始化存储（建表 + 每日备份） ----
    store = get_store()
    store.maybe_backup()

    port = int(opts["port"])
    host = opts["host"]
    app = build_app()

    # ---- 运行信息（status 自检用）+ 退出清理 ----
    _write_server_json(port)
    atexit.register(_clear_server_json)

    lan = _get_lan_ips()
    if host in ("0.0.0.0", "::", ""):
        print(f"  ➜ 管理页面:  http://localhost:{port}/admin")
        for ip in lan:
            print(f"              http://{ip}:{port}/admin")
        if not lan:
            print("              （未检测到内网 IP）")
    else:
        print(f"  ➜ 管理页面:  http://{host}:{port}/admin")
    first_ip = lan[0] if lan else "localhost"
    print(f"  ➜ 客户端接入: cbhcli login {first_ip}:{port} <密钥>")
    print(f"  📦 数据目录: {data_dir()}")
    if opts["tls_cert"]:
        print("  🔒 HTTPS 已启用")
    print("  Press Ctrl+C to stop\n")

    import uvicorn
    ssl_kwargs = {}
    if opts["tls_cert"] and opts["tls_key"]:
        ssl_kwargs = {"ssl_certfile": opts["tls_cert"],
                      "ssl_keyfile": opts["tls_key"]}
    try:
        uvicorn.run(app, host=host, port=port, log_level="warning",
                    **ssl_kwargs)
    except KeyboardInterrupt:
        pass
    finally:
        _clear_server_json()
    print("\n管理服务已停止")
    return 0


def run_admin_entry() -> None:
    """cbhcli-admin 命令入口（pyproject [project.scripts]）。"""
    sys.exit(run_admin(sys.argv[1:]))


# ===================================================================
#  status 自检
# ===================================================================

def run_admin_status() -> int:
    """cbhcli-admin status / cbhcli admin status —— 服务端连通/健康自检。"""
    p = _server_json_path()
    info = {}
    try:
        if p.exists():
            info = json.loads(p.read_text(encoding="utf-8")) or {}
    except Exception:
        info = {}

    port = int(info.get("port", DEFAULT_PORT) or DEFAULT_PORT)
    pid = int(info.get("pid", 0) or 0)
    alive = False
    if pid:
        try:
            os.kill(pid, 0)
            alive = True
        except ProcessLookupError:
            alive = False
        except PermissionError:
            alive = True
        except Exception:
            alive = False

    reachable = False
    detail = ""
    try:
        with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/api/ping", timeout=2.0) as resp:
            body = json.loads(resp.read().decode("utf-8", errors="replace"))
        reachable = bool(body.get("ok"))
        detail = f"{body.get('service', '')} {body.get('version', '')}".strip()
    except Exception as e:
        detail = str(e)

    if not info and not reachable:
        print("ℹ️  CBHCLI Admin 服务未启动。")
        print("   启动命令: cbhcli admin（或 cbhcli-admin）")
        return 1

    if info and not alive and not reachable:
        print("⚠️  检测到残留的运行信息（server.json），但进程已退出。")
        print(f"   端口: {port}   PID: {pid}（不存在）")
        print("   可能原因: 服务异常退出 / 被 kill；可删除残留文件: "
              f"{p}")
        print("   重新启动: cbhcli admin")
        return 1

    store = get_store()
    ov = store.overview()
    started = float(info.get("ts", 0) or 0)
    uptime = time.time() - started if started else 0
    last_event = ov.get("last_event_ts", 0)
    print("✅ CBHCLI Admin 服务运行中")
    print(f"   端口:     {port}（{'监听正常' if reachable else '探测失败'}）")
    print(f"   版本:     {info.get('version', SERVER_VERSION)}（protocol {PROTOCOL}）")
    if started:
        print(f"   启动时间: {time.strftime('%Y-%m-%d %H:%M', time.localtime(started))}"
              f"（已运行 {_fmt_duration(uptime)}）")
    print(f"   数据目录: {data_dir()}（DB {_fmt_size(store.db_size_bytes())}"
          f" / 快照 {store.snapshot_count()} 个）")
    if last_event:
        print(f"   最近上报: {_fmt_duration(time.time() - last_event)}前")
    else:
        print("   最近上报: （暂无）")
    if not reachable:
        print(f"   ⚠️  端口探测失败: {detail}")
    return 0 if reachable else 1


def _fmt_duration(seconds: float) -> str:
    try:
        seconds = int(max(0, seconds))
        if seconds < 60:
            return f"{seconds} 秒"
        if seconds < 3600:
            return f"{seconds // 60} 分钟"
        h = seconds // 3600
        m = (seconds % 3600) // 60
        if h < 24:
            return f"{h} 小时" + (f" {m} 分钟" if m else "")
        return f"{h // 24} 天 {h % 24} 小时"
    except Exception:
        return "?"


def _fmt_size(n: int) -> str:
    try:
        n = float(n or 0)
        if n < 1024:
            return f"{int(n)}B"
        if n < 1024 ** 2:
            return f"{n / 1024:.1f}KB"
        if n < 1024 ** 3:
            return f"{n / 1024 ** 2:.1f}MB"
        return f"{n / 1024 ** 3:.2f}GB"
    except Exception:
        return "?"
