#!/usr/bin/env python3
"""输入行边界/宽字符回归（v5.5.0 修复）

用法：
    /usr/bin/python3 tests/ui/check_input_boundary.py

背景（用户实测）：第一轮对话后，第二轮输入出现"最前面有一位退格退不掉、位置错乱"。
根因方向：主区/状态栏写入**终端最后一列**时，宽字符（CJK/emoji）会让终端自动换行，
在最底部触发行滚动 -> 整屏上移 -> 输入行与模型错位（残留格子无法用退格清掉）。

修复：布局最右预留 1 列（VSplit + 1 列空白），主区按 `cols-1` 折行 —— 任何行都不写最后一格。

本测试用 **pyte**（列精确）重放终端输出，在 40 列窄终端下：
  - 主区塞满中文长行（边界压力）
  - 输入 "你好abc" → 断言输入行是 `❯ 你好abc`、光标列 = 9、行号不变
  - 退格 3 次 + 输入 xy → 断言 `❯ 你好xy`、光标列 = 8、行号不变
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

try:
    import pyte
except Exception:  # pragma: no cover
    pyte = None

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ROWS, COLS = 12, 40

CHILD = r'''
import asyncio
from cbhcli_pkg.core.ui.shell import FullScreenUI

async def main():
    # 状态栏含中文且贴近宽度边界
    ui = FullScreenUI(status_provider=lambda: [
        ("", "标准 │ 模型: 中文模型名 │ 上下文: ██████████ 123,456/1,000,000"),
        ("", "\n链条: 🔗 中文链条名 › 中文代理 │ 路径: /home/用户/分析建模平台"),
    ])
    ui.install_sink()
    task = asyncio.create_task(ui.app.run_async())
    await asyncio.sleep(0.5)
    # 主区塞满中文（宽度边界压力）
    for i in range(40):
        print(f"AI-{i:02d} " + "中文内容测试" * 4)
    ui.invalidate()
    await asyncio.sleep(1.0)
    print("READY")
    await asyncio.sleep(8.0)
    ui.close_input()
    ui.app.exit()
    try:
        await asyncio.wait_for(task, timeout=5)
    except Exception:
        pass
    ui.uninstall_sink()

asyncio.run(main())
'''


def main() -> int:
    if pyte is None:
        print("⚠️ 跳过：未安装 pyte")
        return 0

    pid, m = pty.fork()
    if pid == 0:
        env = os.environ.copy()
        env.update(TERM="xterm-256color", COLUMNS=str(COLS), LINES=str(ROWS),
                   PYTHONPATH=ROOT, CBHCLI_MOUSE="on")
        os.chdir("/tmp")
        os.execvpe(sys.executable, [sys.executable, "-c", CHILD], env)

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

    def snap():
        sc = pyte.Screen(COLS, ROWS)
        st = pyte.ByteStream(sc)
        st.feed(bytes(buf))
        rows = ["".join(sc.buffer[y][x].data for x in range(COLS)) for y in range(ROWS)]
        return rows, sc.cursor.y, sc.cursor.x

    t0 = time.time()
    while time.time() - t0 < 12 and b"READY" not in buf:
        pump(0.15)
    time.sleep(0.6)
    pump(0.3)

    rows0, y0, _ = snap()

    def input_row(rows):
        for i, r in enumerate(rows):
            if "❯" in r:
                return i, r.rstrip()
        return -1, ""

    y_in, row_in = input_row(rows0)

    os.write(m, "你好abc".encode())
    time.sleep(0.8)
    pump(0.4)
    rows1, y1, x1 = snap()
    y_in1, row_in1 = input_row(rows1)

    os.write(m, b"\x7f\x7f\x7f")
    time.sleep(0.5)
    pump(0.3)
    os.write(m, b"xy")
    time.sleep(0.6)
    pump(0.3)
    rows2, y2, x2 = snap()
    y_in2, row_in2 = input_row(rows2)

    text_all = bytes(buf).decode("utf-8", "replace")
    try:
        os.write(m, b"quit\r")
    except OSError:
        pass
    time.sleep(0.8)
    pump(0.4)
    try:
        os.kill(pid, signal.SIGKILL)
    except Exception:
        pass
    try:
        os.waitpid(pid, 0)
    except Exception:
        pass

    print(f"初始输入行: y={y_in} {row_in!r}")
    print(f"输入你好abc: y={y_in1} x={x1} {row_in1!r}")
    print(f"退格3+xy:    y={y_in2} x={x2} {row_in2!r}")

    ok = True

    def check(name, cond):
        nonlocal ok
        print(("  ✅ " if cond else "  ❌ ") + name)
        ok = ok and cond

    check("输入行存在且带 ❯ 前缀", y_in >= 0 and row_in.startswith("❯"))
    check("输入 你好abc 后行内容正确", row_in1.endswith("❯ 你好abc"))
    check("光标列正确（=9）", x1 == 9)
    check("输入行行号不变（无整屏滚动）", y_in == y_in1 == y_in2)
    check("退格+xy 后内容正确（❯ 你好xy）", row_in2.endswith("❯ 你好xy"))
    check("光标列正确（=8）", x2 == 8)
    check("无 Traceback", "Traceback" not in text_all)

    print("\n" + ("✅ PASS" if ok else "❌ FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
