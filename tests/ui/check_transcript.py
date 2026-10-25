#!/usr/bin/env python3
"""P6 门禁：transcript 落盘（契约 S-11）

用法：
    python tests/ui/check_transcript.py

做法：
    - 最小 FullScreenUI，`start_transcript(path)`；
    - 写入普通行、思考窗口、spinner 活动行；
    - 断言 transcript 只含**已提交的普通行**（思考/spinner 不得出现）；
    - 断言文件 0600、目录 0700。
"""

from __future__ import annotations

import fcntl
import os
import pty
import select
import signal
import struct
import sys
import tempfile
import termios
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

CHILD = r"""
import asyncio, os, sys, threading, time
sys.path.insert(0, os.environ["ROOT"])
from cbhcli_pkg.core.ui.shell import FullScreenUI
from cbhcli_pkg.core.thinking_display import ThinkingDisplay
from cbhcli_pkg.core.ui.sink import get_sink

PATH = os.environ["TRANSCRIPT"]
ui = FullScreenUI(status_provider=lambda: [("class:bottom-toolbar", "T")])
ui.start_transcript(PATH)

def scenario():
    time.sleep(0.6)
    ui.feed("normal-line-1\n")
    ui.feed("normal-line-2\n")
    # 思考窗口（临时区域，不得进 transcript）
    td = ThinkingDisplay(max_lines=8)
    td.enabled = True
    td.start_thinking()
    td.add_content("THINKING-SHOULD-NOT-LOG\n")
    td.finish_thinking()
    # spinner 活动行（临时区域）
    sink = get_sink()
    sink.live_line("SPINNER-SHOULD-NOT-LOG")
    sink.live_line(erase=True)
    ui.feed("normal-line-3\n")
    time.sleep(0.2)
    ui.close_transcript()
    loop = getattr(ui.app, "loop", None)
    if loop is not None and loop.is_running():
        loop.call_soon_threadsafe(ui.app.exit)
    else:
        ui.app.exit()

threading.Thread(target=scenario, daemon=True).start()
asyncio.run(ui.run_async())
"""


def main() -> int:
    tmpdir = tempfile.mkdtemp(prefix="cbhcli_transcript_")
    path = os.path.join(tmpdir, "sub", "t.log")

    pid, master = pty.fork()
    if pid == 0:
        os.chdir(ROOT)
        env = os.environ.copy()
        env.update(TERM="xterm-256color", COLUMNS="80", LINES="24",
                   PYTHONPATH=ROOT, ROOT=ROOT, TRANSCRIPT=path)
        try:
            os.execvpe(sys.executable, [sys.executable, "-c", CHILD], env)
        except Exception:
            os._exit(127)
    fcntl.ioctl(master, termios.TIOCSWINSZ, struct.pack("HHHH", 24, 80, 0, 0))

    buf = bytearray()
    t0 = time.time()
    status = None
    while time.time() - t0 < 15:
        r, _, _ = select.select([master], [], [], 0.12)
        if r:
            try:
                d = os.read(master, 65536)
            except OSError:
                d = b""
            if d:
                buf.extend(d)
                if b"\x1b[6n" in d:
                    try:
                        os.write(master, b"\x1b[24;1R")
                    except OSError:
                        pass
        w, s = os.waitpid(pid, os.WNOHANG)
        if w == pid:
            status = s
            break
    if status is None:
        try:
            os.kill(pid, signal.SIGKILL)
        except Exception:
            pass
        try:
            _, status = os.waitpid(pid, 0)
        except Exception:
            status = -1
    try:
        os.close(master)
    except OSError:
        pass

    content = ""
    exists = os.path.exists(path)
    if exists:
        with open(path, encoding="utf-8") as f:
            content = f.read()
    mode = (os.stat(path).st_mode & 0o777) if exists else None
    dmode = (os.stat(os.path.dirname(path)).st_mode & 0o777) if exists else None

    checks = [
        ("文件存在", exists),
        ("普通行已写入", "normal-line-1" in content and "normal-line-3" in content),
        ("思考内容未写入", "THINKING-SHOULD-NOT-LOG" not in content),
        ("spinner 未写入", "SPINNER-SHOULD-NOT-LOG" not in content),
        ("文件 0600", mode == 0o600),
        ("目录 0700", dmode == 0o700),
        ("进程正常退出", status is not None and os.WIFEXITED(status) and os.WEXITSTATUS(status) == 0),
    ]

    print("=" * 70)
    print("P6 门禁：transcript")
    print("=" * 70)
    print(f"文件: {path}  大小: {len(content)}B")
    print("--- 内容 ---")
    print(content)
    print("-----------")
    ok = True
    for name, cond in checks:
        print(f"  {'✅' if cond else '❌'} {name}")
        ok = ok and cond
    print("\n✅ PASS" if ok else "\n❌ FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
