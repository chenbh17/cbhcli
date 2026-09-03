"""CLI -> Web 运行状态通知（v5.3.1+）

CLI 与 Web 是两个独立进程，共享同一份会话历史文件。本模块让 CLI 对话时
把运行状态实时推送给本机所有运行中的 cbhcli Web 实例：

- Web 服务启动时在 ~/.cbhcli/web_servers.json 注册 {端口: {pid, ts}}
- CLI 每轮对话开始/心跳（30s）/结束时调用 notify_web() 上报
- Web 侧边栏显示"CLI 运行中"徽标，打开会话可见只读实时视图（轮级刷新）

所有网络调用均短超时 + 静默失败，绝不影响 CLI 正常对话。
"""
import json
import os
import threading
import time
import urllib.request

try:
    from cbhcli_pkg.config.global_config import CBHCLI_DIR
except Exception:  # pragma: no cover
    CBHCLI_DIR = os.path.expanduser("~/.cbhcli")

_SERVERS_FILE = os.path.join(str(CBHCLI_DIR), "web_servers.json")
_ENTRY_TTL = 24 * 3600  # 注册条目最长保留时间（防时间戳异常残留）


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except Exception:
        return False


def _load_server_ports() -> list:
    """读取本机存活 Web 实例的端口列表（清理死进程/过期条目）。"""
    try:
        with open(_SERVERS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f) or {}
    except Exception:
        return []
    ports = []
    now = time.time()
    for port, info in data.items():
        try:
            if not isinstance(info, dict):
                continue
            if now - float(info.get("ts", 0)) > _ENTRY_TTL:
                continue
            if not _pid_alive(int(info.get("pid", 0))):
                continue
            ports.append(int(port))
        except Exception:
            continue
    return ports


def notify_web(agent_name: str, session_id: str, status: str,
               workspace: str = "", title: str = "") -> None:
    """向所有存活 Web 实例推送会话状态（各 0.5s 超时，静默失败）。

    Args:
        status: "running"（对话开始/心跳）或 "idle"（对话结束）
    """
    if not agent_name or not session_id:
        return
    ports = _load_server_ports()
    if not ports:
        return
    payload = json.dumps({
        "agent_name": agent_name,
        "session_id": session_id,
        "status": status,
        "workspace": workspace or "",
        "title": title or "",
    }).encode("utf-8")
    for port in ports:
        try:
            req = urllib.request.Request(
                f"http://127.0.0.1:{port}/api/cli_notify",
                data=payload, method="POST",
                headers={"Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=0.5).read(1)
        except Exception:
            pass


def notify_web_async(agent_name: str, session_id: str, status: str,
                     workspace: str = "", title: str = "") -> None:
    """非阻塞版通知（守护线程，不占用终端交互主流程）。"""
    threading.Thread(
        target=notify_web,
        args=(agent_name, session_id, status, workspace, title),
        daemon=True).start()


def session_title_of(session) -> str:
    """提取会话首条用户消息作为通知标题（失败返回空串）。"""
    try:
        for m in getattr(session, "messages", []):
            if getattr(m, "role", "") == "user":
                content = getattr(m, "content", "") or ""
                if isinstance(content, str) and content.strip():
                    return " ".join(content.split())[:50]
    except Exception:
        pass
    return ""
