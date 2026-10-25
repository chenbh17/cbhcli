#!/usr/bin/env python3
"""命令补全单列显示回归（v5.5.0）

用法：
    /usr/bin/python3 tests/ui/check_completion_column.py

输入 `/` 打开命令补全菜单，用 pyte 重放屏幕后断言：
  - 菜单确实出现（至少 5 行命令）；
  - 每一行最多只有一个 `/命令`（不是多列平铺）。
"""

from __future__ import annotations

import fcntl
import os
import pty
import re
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
                   COLUMNS=str(COLS), LINES=str(ROWS), PYTHONPATH=ROOT)
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
    while time.time() - t0 < 4:
        pump(0.2)
    os.write(m, b"/")
    time.sleep(1.2)
    pump(0.5)

    sc = pyte.Screen(COLS, ROWS)
    pyte.ByteStream(sc).feed(bytes(buf))
    rows = ["".join(sc.buffer[y][x].data for x in range(COLS)) for y in range(ROWS)]

    # 命令菜单行：包含 /命令 且不是输入行
    cmd_re = re.compile(r"/[a-z][a-z_\-]*")
    menu_rows = []
    for y, r in enumerate(rows):
        if r.lstrip().startswith("❯"):
            continue
        if any(k in r for k in ("路径:", "模型:", "上下文:", "链条:")):
            continue
        if cmd_re.search(r) and ("│" in r or "   /" in r or r.lstrip().startswith("/")):
            menu_rows.append((y, r.rstrip(), cmd_re.findall(r)))

    multi = [(y, r, cmds) for y, r, cmds in menu_rows if len(set(cmds)) > 1]

    print(f"菜单行数={len(menu_rows)}，多命令行={len(multi)}")
    for y, r, cmds in multi[:5]:
        print(f"  MULTI {y}|{r[:90]!r} {cmds}")
    for y, r, cmds in menu_rows[:8]:
        print(f"  {y:2d}|{r[:80]!r}  cmds={cmds}")

    ok = True

    def check(name, cond):
        nonlocal ok
        print(("  ✅ " if cond else "  ❌ ") + name)
        ok = ok and cond

    check("补全菜单出现（>=5 行）", len(menu_rows) >= 5)
    check("每行最多一个命令（单列）", not multi)
    check("无 Traceback", b"Traceback" not in bytes(buf))

    try:
        os.write(m, b"\x03")
    except OSError:
        pass
    time.sleep(0.3)
    try:
        os.write(m, b"quit\r")
    except OSError:
        pass
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
    check("退出码 0", os.WIFEXITED(status) and os.WEXITSTATUS(status) == 0)

    print("\n" + ("✅ PASS" if ok else "❌ FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
