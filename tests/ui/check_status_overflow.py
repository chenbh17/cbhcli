#!/usr/bin/env python3
"""状态栏/宽字符边界回归（v5.5.0）

用法：
    /usr/bin/python3 tests/ui/check_status_overflow.py

背景（用户实测）：状态栏出现长提示（原先是 F2 鼠标开关提示）后，最底部一行被宽字符
写满最后一列 → 终端自动换行 → 整屏上滚 → 输入行错位/残格（`❯ nnihao`）。

修复：布局最右预留 2 列 + 状态栏按显示宽度截断到 `cols-4`（宽字符安全）。
断言（pyte 列精确重放，多种宽度）：
  1. 每一行的**最后 2 列**都是空格（任何内容都没写进预留列）；
  2. 状态栏变长 + 输入 `nihao` 后，输入行是 `❯ nihao`、行号稳定。

注：v5.5.0 取消 F2 后 `_set_notice` 已移除，这里改用**可变 status_provider**
模拟"状态栏变长"（同样的边界条件，不依赖已删除的 F2 API）。
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

try:
    import pyte
except Exception:  # pragma: no cover
    pyte = None

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

CHILD = r'''
import asyncio, sys
from cbhcli_pkg.core.ui.shell import FullScreenUI

async def main():
    state = {"long": False}

    def status():
        if state["long"]:
            # 模拟"状态栏变长"（原 F2 提示的位置，v5.5.0 已无 F2）
            return [
                ("", "标准 │ 模型: 中文模型名 │ 上下文: ██████████ 123,456/1,000,000"),
                ("", "\n鼠标滚轮滚动历史（按住 Shift 拖动选中）"),
            ]
        return [
            ("", "标准 │ 模型: 中文模型名 │ 上下文: ██████████ 123,456/1,000,000"),
            ("", "\n链条: 中文链条名 › 中文代理 │ 路径: /home/用户/分析建模平台"),
        ]

    ui = FullScreenUI(status_provider=status)
    ui.install_sink()
    task = asyncio.create_task(ui.app.run_async())
    await asyncio.sleep(0.5)
    for i in range(20):
        print(f"AI-{i:02d} " + "中文内容测试" * 3)
    ui.invalidate()
    await asyncio.sleep(0.6)
    # 状态栏变长（与 F2 提示相同的边界条件）
    state["long"] = True
    ui.invalidate()
    await asyncio.sleep(0.6)
    print("NOTICE-SHOWN")
    await asyncio.sleep(6.0)
    ui.close_input()
    ui.app.exit()
    try:
        await asyncio.wait_for(task, timeout=5)
    except Exception:
        pass
    ui.uninstall_sink()

asyncio.run(main())
'''


def run_case(rows: int, cols: int) -> bool:
    pid, m = pty.fork()
    if pid == 0:
        env = os.environ.copy()
        env.update(TERM="xterm-256color", COLUMNS=str(cols), LINES=str(rows),
                   CBHCLI_MOUSE="on", PYTHONPATH=ROOT)
        os.chdir("/tmp")
        os.execvpe(sys.executable, [sys.executable, "-c", CHILD], env)

    fcntl.ioctl(m, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
    buf = bytearray()

    def pump(t=0.1):
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
                        os.write(m, f"\x1b[{rows};1R".encode())
                    except OSError:
                        pass

    def screen():
        sc = pyte.Screen(cols, rows)
        pyte.ByteStream(sc).feed(bytes(buf))
        return (["".join(sc.buffer[y][x].data for x in range(cols)) for y in range(rows)],
                sc.cursor.y)

    t0 = time.time()
    while time.time() - t0 < 10 and b"NOTICE-SHOWN" not in buf:
        pump(0.1)
    time.sleep(0.5)
    pump(0.3)

    rows0, y0 = screen()
    input_rows0 = [i for i, r in enumerate(rows0) if "❯" in r]
    y_in = input_rows0[0] if input_rows0 else -1

    os.write(m, "nihao".encode())
    time.sleep(0.8)
    pump(0.4)
    rows1, y1 = screen()
    input_rows1 = [i for i, r in enumerate(rows1) if "❯" in r]
    line1 = rows1[input_rows1[0]].rstrip() if input_rows1 else ""

    try:
        os.write(m, b"quit\r")
    except OSError:
        pass
    time.sleep(0.6)
    pump(0.3)
    try:
        os.kill(pid, signal.SIGKILL)
    except Exception:
        pass
    try:
        os.waitpid(pid, 0)
    except Exception:
        pass

    ok = True

    def check(name, cond):
        nonlocal ok
        print(("  ✅ " if cond else "  ❌ ") + name)
        ok = ok and cond

    # 最后 2 列必须全空
    bad = []
    for y, r in enumerate(rows1):
        if r[-2:].strip():
            bad.append((y, r[-4:]))
    print(f"  {cols}x{rows}: 输入行 y={y_in}→{input_rows1[0] if input_rows1 else -1} {line1!r}；"
          f"末两列非空行={bad[:3]}")
    check(f"{cols}x{rows} 最后 2 列保持空白", not bad)
    check(f"{cols}x{rows} 输入行正确（❯ nihao）", line1.endswith("❯ nihao"))
    check(f"{cols}x{rows} 输入行行号不变", y_in >= 0 and input_rows1 and y_in == input_rows1[0])
    return ok


def main() -> int:
    if pyte is None:
        print("⚠️ 跳过：未安装 pyte")
        return 0
    ok = True
    for rows, cols in [(14, 40), (24, 80), (30, 120)]:
        print(f"--- {cols} 列 ---")
        ok = run_case(rows, cols) and ok
    print("\n" + ("✅ PASS" if ok else "❌ FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
