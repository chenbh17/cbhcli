#!/usr/bin/env python3
"""P7 门禁：编辑键（Ctrl+A/E/U/K/W、历史 ↑）

用法：
    python tests/ui/check_edit_keys.py

做法：
    - 最小 FullScreenUI，提交时把文本回显为 `SUBMIT:<text>`（不接 AI）；
    - 一次性发送按键序列，逐条断言编辑结果；
    - 最后 Ctrl+D 退出。
"""

from __future__ import annotations

import fcntl
import os
import pty
import select
import signal
import struct
import sys
import termios
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

CHILD = r"""
import asyncio, os, sys
sys.path.insert(0, os.environ["ROOT"])
from cbhcli_pkg.core.ui.shell import FullScreenUI

ui = FullScreenUI(status_provider=lambda: [("class:bottom-toolbar", "KEYS")])

def on_submit(text):
    ui.feed("\nSUBMIT:%s\n" % text)

ui.on_submit = on_submit
ui.feed("READY-KEYS\n")
asyncio.run(ui.run_async())
"""


def main() -> int:
    pid, master = pty.fork()
    if pid == 0:
        os.chdir(ROOT)
        env = os.environ.copy()
        env.update(TERM="xterm-256color", COLUMNS="80", LINES="24",
                   PYTHONPATH=ROOT, ROOT=ROOT)
        try:
            os.execvpe(sys.executable, [sys.executable, "-c", CHILD], env)
        except Exception:
            os._exit(127)
    fcntl.ioctl(master, termios.TIOCSWINSZ, struct.pack("HHHH", 24, 80, 0, 0))

    buf = bytearray()
    t0 = time.time()
    sent = False
    status = None

    # 逐条发送（每次按键之间留出处理时间；\r 之后多等一会，让
    # validate/history/reset 完成）
    steps = [
        (b"abc", 0.15), (b"\x01", 0.10), (b"X", 0.10), (b"\r", 0.50),
        (b"abc", 0.15), (b"\x01", 0.10), (b"\x05", 0.10), (b"X", 0.10), (b"\r", 0.50),
        (b"zzz", 0.15), (b"\x15", 0.10), (b"abc", 0.15), (b"\r", 0.50),
        (b"abc", 0.15), (b"\x01", 0.10), (b"\x0b", 0.10), (b"Y", 0.10), (b"\r", 0.50),
        (b"foo bar", 0.15), (b"\x17", 0.10), (b"\r", 0.60),
        (b"\x1b[A", 0.30), (b"\r", 0.50),
        (b"\x04", 0.20),
    ]
    step_idx = 0
    next_at = 0.0

    while time.time() - t0 < 30:
        r, _, _ = select.select([master], [], [], 0.08)
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
        if sent and step_idx < len(steps) and time.time() >= next_at:
            data, delay = steps[step_idx]
            os.write(master, data)
            step_idx += 1
            next_at = time.time() + delay
        if not sent and b"READY-KEYS" in buf:
            time.sleep(0.3)
            sent = True
            next_at = time.time()
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

    text = bytes(buf)
    checks = [
        ("Ctrl+A", b"SUBMIT:Xabc" in text),
        ("Ctrl+A + Ctrl+E", b"SUBMIT:abcX" in text),
        ("Ctrl+U", b"SUBMIT:abc" in text),
        ("Ctrl+A + Ctrl+K", b"SUBMIT:Y" in text),
        ("Ctrl+W", b"SUBMIT:foo" in text),
        ("历史 ↑", text.count(b"SUBMIT:foo") >= 2),
        ("Ctrl+D 退出", status is not None and os.WIFEXITED(status) and os.WEXITSTATUS(status) == 0),
        ("无 Traceback", b"Traceback" not in text),
    ]

    print("=" * 70)
    print("P7 门禁：编辑键")
    print("=" * 70)
    print(f"输出 {len(text)}B  状态 {status}")
    ok = True
    for name, cond in checks:
        print(f"  {'✅' if cond else '❌'} {name}")
        ok = ok and cond
    if not ok:
        print("--- 输出片段 ---")
        for line in text.decode("utf-8", "replace").splitlines():
            if "SUBMIT" in line:
                print(line)
    print("\n✅ PASS" if ok else "\n❌ FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
