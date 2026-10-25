#!/usr/bin/env python3
"""交互式命令 UI 回归（v5.5.0 补充）

用法：
    /usr/bin/python3 tests/ui/check_interactive.py

覆盖：
  1. `/resume` 等待输入时**不显示**「运行中」动画；
  2. `/login` 的提问与输入在同一行（旧 PromptSession 行为一致）；
  3. 交互结束退出后显式复位鼠标协议（含 1002/1007，避免滚轮失效）。
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

import pyte

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ROWS, COLS = 30, 100


def main() -> int:
    pid, m = pty.fork()
    if pid == 0:
        env = os.environ.copy()
        env.update(CBHCLI_UI="fullscreen", TERM="xterm-256color",
                   COLUMNS=str(COLS), LINES=str(ROWS),
                   PYTHONPATH=ROOT)
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

    def wait_for(marker: bytes, timeout=25):
        end = time.time() + timeout
        while time.time() < end:
            if marker in buf:
                return True
            pump(0.15)
        return marker in buf

    def screen():
        sc = pyte.Screen(COLS, ROWS)
        pyte.ByteStream(sc).feed(bytes(buf))
        return ["".join(sc.buffer[y][x].data for x in range(COLS)) for y in range(ROWS)]

    def row_with(text):
        rows = screen()
        for i, r in enumerate(rows):
            if text in r:
                return i, r
        return -1, ""

    ok = True

    def check(name, cond):
        nonlocal ok
        print(("  ✅ " if cond else "  ❌ ") + name)
        ok = ok and cond

    check("启动", wait_for("AI 驱动的终端助手".encode()))

    # /resume：等待输入期间不能显示运行中
    start_i = len(buf)
    os.write(m, b"/resume\r")
    check("/resume 提示出现", wait_for("请选择 [会话编号".encode()))
    time.sleep(0.4)
    pump(0.2)
    seg = "\n".join(screen())
    check("/resume 等待输入不显示运行中", "运行中" not in seg)
    y, row = row_with("请选择 [会话编号")
    check("/resume 提示与输入同一行", y >= 0 and row.rstrip().endswith(":"))
    os.write(m, b"0\r")
    check("/resume 取消", wait_for("已取消".encode()))

    # /login：不提交任何凭据，只检查提问与输入位置
    start_i = len(buf)
    os.write(m, b"/login\r")
    check("/login 提问出现", wait_for("要重新登录吗".encode(), 15))
    time.sleep(0.4)
    pump(0.2)
    seg = "\n".join(screen())
    check("/login 等待输入不显示运行中", "运行中" not in seg)
    y, row = row_with("要重新登录吗")
    check("/login 提示与输入同一行", y >= 0 and "[y/N]:" in row)
    os.write(m, b"n\r")
    check("/login 取消", wait_for("现有凭据保持不变".encode(), 15))

    # 退出后应显式复位所有鼠标模式
    os.write(m, b"quit\r")
    end = time.time() + 10
    status = None
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
    text = bytes(buf)
    check("退出码 0", os.WIFEXITED(status) and os.WEXITSTATUS(status) == 0)
    check("退出含 1002l", b"\x1b[?1002l" in text)
    check("退出含 1007l", b"\x1b[?1007l" in text)
    check("退出关闭鼠标上报", b"\x1b[?1000l" in text)
    check("无 Traceback", b"Traceback" not in text)

    print("\n" + ("✅ PASS" if ok else "❌ FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
