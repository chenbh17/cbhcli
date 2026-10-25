#!/usr/bin/env python3
"""退出后终端鼠标/滚动模式恢复（v5.5.0）

用法：
    /usr/bin/python3 tests/ui/check_exit_mouse.py

两个退出路径：quit / Ctrl+D。
断言：
  - 退出码 0；
  - 输出含 `?1049l`、`?1000l`、`?1002l`、`?1007l`；
  - 退出后不再重新开启 `?1000h`。
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
ROWS, COLS = 24, 80


def run_case(name: str, actions) -> bool:
    pid, m = pty.fork()
    if pid == 0:
        env = os.environ.copy()
        env.update(CBHCLI_UI="fullscreen", TERM="xterm-256color",
                   COLUMNS=str(COLS), LINES=str(ROWS),
                   CBHCLI_MOUSE="on", PYTHONPATH=ROOT)
        os.chdir(ROOT)
        os.execvpe(sys.executable, [sys.executable, "-m", "cbhcli_pkg"], env)

    fcntl.ioctl(m, termios.TIOCSWINSZ, struct.pack("HHHH", ROWS, COLS, 0, 0))
    buf = bytearray()

    def pump(t=0.15):
        r, _, _ = select.select([m], [], [], t)
        if r:
            try:
                d = os.read(m, 65536)
            except OSError:
                d = b""
            if d:
                buf.extend(d)
                if b"\x1b[6n" in d:
                    try:
                        os.write(m, f"\x1b[{ROWS};1R".encode())
                    except OSError:
                        pass

    t0 = time.time()
    while time.time() - t0 < 20 and "AI 驱动的终端助手".encode() not in buf:
        pump(0.2)
    time.sleep(0.3)
    for data, delay in actions:
        try:
            os.write(m, data)
        except OSError:
            pass
        time.sleep(delay)
        pump(0.2)

    status = None
    end = time.time() + 12
    while time.time() < end and status is None:
        pump(0.2)
        w, st = os.waitpid(pid, os.WNOHANG)
        if w == pid:
            status = st
            break
    if status is None:
        try:
            os.kill(pid, signal.SIGKILL)
        except Exception:
            pass
        _, status = os.waitpid(pid, 0)
    try:
        os.close(m)
    except OSError:
        pass

    data = bytes(buf)
    ok = True

    def check(label, cond):
        nonlocal ok
        print(("  ✅ " if cond else "  ❌ ") + f"{name}: {label}")
        ok = ok and cond

    check("退出码 0", os.WIFEXITED(status) and os.WEXITSTATUS(status) == 0)
    check("退出备用屏", b"\x1b[?1049l" in data)
    check("关闭鼠标上报", b"\x1b[?1000l" in data)
    check("关闭 DRAG", b"\x1b[?1002l" in data)
    check("关闭 alternate scroll", b"\x1b[?1007l" in data)
    tail = data[data.rfind(b"\x1b[?1049l"):]
    check("退出后未重新开启鼠标", b"\x1b[?1000h" not in tail)
    check("无 Traceback", b"Traceback" not in data)
    return ok


def main() -> int:
    ok = True
    ok = run_case("quit", [(b"quit\r", 0.5)]) and ok
    ok = run_case("Ctrl+D", [(b"\x04", 0.5)]) and ok
    print("\n" + ("✅ PASS" if ok else "❌ FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
