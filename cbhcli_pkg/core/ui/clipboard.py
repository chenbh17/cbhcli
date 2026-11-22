"""剪贴板多通道复制（v5.5.1 迭代：应用内拖选 + Ctrl+C 复制）

终端环境差异大，单一通道无法覆盖所有场景，按以下顺序全部尝试：

1. **Jupyter 桥文件** `~/.cbhcli/clipboard.txt`（0600，原子写）——
   cbhcli-jupyter 插件前端轮询该文件并写入**浏览器剪贴板**。
   这是 JupyterLab 终端（xterm.js）唯一可行通道：实测其 xterm.js
   未注册 OSC 52 处理器，服务器侧 xclip 也到不了用户浏览器。
2. **OSC 52**——kitty / WezTerm / Windows Terminal / iTerm2 / Alacritty /
   foot 等现代终端直接写系统剪贴板（SSH 远程场景同样有效）。
   不支持的终端会静默忽略该序列，无副作用。
3. **系统工具**——wl-copy（Wayland）/ xclip / xsel（X11）/ pbcopy（macOS），
   适用于 cbhcli 运行在本地桌面会话的场景。

返回最优通道描述（供状态栏提示）；任何一步失败都不抛异常。
"""

from __future__ import annotations

import base64
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Optional

__all__ = ["copy_text", "clipboard_file_path"]

#: OSC 52 单次上限（部分终端对序列长度有限制，超长走其它通道）
_OSC52_MAX_CHARS = 100_000

_JUPYTER_ENV: Optional[bool] = None


def clipboard_file_path() -> Path:
    """Jupyter 桥文件路径（插件 ClipboardPendingHandler 读取同一文件）"""
    return Path.home() / ".cbhcli" / "clipboard.txt"


def _in_jupyter_terminal() -> bool:
    """探测祖先进程是否为 jupyter server（终端由 JupyterLab 拉起）"""
    global _JUPYTER_ENV
    if _JUPYTER_ENV is not None:
        return _JUPYTER_ENV
    found = False
    try:
        pid = os.getpid()
        for _ in range(8):
            try:
                with open(f"/proc/{pid}/stat", "r", encoding="utf-8") as f:
                    # comm 可能含空格/括号：取最后一个 ')' 之后的字段
                    ppid = int(f.read().rsplit(")", 1)[-1].split()[1])
            except Exception:
                break
            if ppid <= 1:
                break
            try:
                with open(f"/proc/{ppid}/cmdline", "rb") as f:
                    cmd = f.read().replace(b"\x00", b" ").lower()
                if b"jupyter" in cmd and (
                        b"jupyter-lab" in cmd or b"jupyter_server" in cmd
                        or b"jupyter-notebook" in cmd or b"jupyter lab" in cmd):
                    found = True
                    break
            except Exception:
                break
            pid = ppid
    except Exception:
        found = False
    _JUPYTER_ENV = found
    return found


def _write_bridge_file(text: str) -> bool:
    """原子写桥文件（插件前端按 mtime+size 检测变化）"""
    try:
        path = clipboard_file_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".clip_")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(text)
            os.chmod(tmp, 0o600)
            os.replace(tmp, str(path))
        except Exception:
            try:
                os.unlink(tmp)
            except Exception:
                pass
            raise
        return True
    except Exception:
        return False


def _send_osc52(text: str, real_stdout) -> bool:
    """OSC 52 写终端剪贴板（不支持的终端静默忽略）"""
    try:
        if len(text) > _OSC52_MAX_CHARS:
            return False
        out = real_stdout or sys.stdout
        if out is None or not out.isatty():
            return False
        b64 = base64.b64encode(text.encode("utf-8")).decode("ascii")
        out.write(f"\033]52;c;{b64}\007")
        out.flush()
        return True
    except Exception:
        return False


def _try_system_tools(text: str) -> bool:
    """本地桌面会话的系统剪贴板工具（第一个成功即返回）"""
    candidates = []
    try:
        if sys.platform == "darwin":
            candidates.append(["pbcopy"])
        else:
            if os.environ.get("WAYLAND_DISPLAY"):
                candidates.append(["wl-copy"])
            if os.environ.get("DISPLAY"):
                candidates.append(["xclip", "-selection", "clipboard"])
                candidates.append(["xsel", "--clipboard", "--input"])
    except Exception:
        return False
    data = text.encode("utf-8")
    for cmd in candidates:
        if shutil.which(cmd[0]) is None:
            continue
        try:
            r = subprocess.run(
                cmd, input=data,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                timeout=2.0)
            if r.returncode == 0:
                return True
        except Exception:
            continue
    return False


def copy_text(text: str, real_stdout=None) -> str:
    """复制文本到剪贴板（多通道），返回通道描述；失败不抛异常。

    Args:
        text: 选区纯文本
        real_stdout: 真实终端流（ScreenSink 接管前的 sys.stdout）
    """
    if not text:
        return ""
    bridge_ok = _write_bridge_file(text)
    osc_ok = _send_osc52(text, real_stdout)
    tool_ok = _try_system_tools(text)

    if tool_ok:
        return "系统剪贴板"
    if bridge_ok and _in_jupyter_terminal():
        return "浏览器剪贴板"
    if osc_ok:
        return "终端剪贴板"
    if bridge_ok:
        return "已存 ~/.cbhcli/clipboard.txt"
    return "复制失败"
