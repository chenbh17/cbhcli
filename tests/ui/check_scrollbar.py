#!/usr/bin/env python3
"""滚动条门禁（v5.5.1 迭代：主区右侧可拖拽滚动条）

用法：
    /usr/bin/python3 tests/ui/check_scrollbar.py

流程（pty 驱动真实 FullScreenUI，24×80）：
    1. 空屏阶段：右侧不应出现滚动条（内容不足一屏）
    2. 写入 200 行 LINE-000..199 → 主区高 15 行，滚动条出现且 thumb 贴底
    3. 拖拽 thumb 从底到顶（SGR down/move/up，x=79 即滚动条列）
       → 视口跳到最旧内容（LINE-000 可见）
    4. 点击轨道底部 → thumb 跳回底部（LINE-199 可见）
    5. 在滚动条列滚轮向上 1 格 → 上移 6 行（灵敏度与主区一致）

断言基于 Screen 重放（prompt_toolkit 是单元格级差分重绘，
原始字节里找整行文本会误判——v5.5.0 教训）。
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

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from cbhcli_pkg.core.ui.lines import Screen  # noqa: E402

CHILD = r'''
import asyncio, sys
sys.path.insert(0, ROOT)
from cbhcli_pkg.core.ui.shell import FullScreenUI

async def main():
    ui = FullScreenUI(status_provider=lambda: [])
    ui.install_sink()
    task = asyncio.create_task(ui.app.run_async())
    await asyncio.sleep(1.0)      # 空屏阶段（父进程断言无滚动条）
    for i in range(200):
        print(f"LINE-{i:03d}")
    sys.stdout.flush()
    ui.invalidate()
    await asyncio.sleep(14.0)
    ui.close_input()
    ui.app.exit()
    try:
        await asyncio.wait_for(task, timeout=5)
    except Exception:
        pass
    ui.uninstall_sink()

asyncio.run(main())
'''.replace("ROOT", repr(ROOT))

SB_X = 79          # 滚动条列（1-based）：0-based 78 = cols-2
MAIN_H = 15        # 24 行 - 1 状态栏 - 8 输入区（空缓冲预留补全高度）


def set_size(fd, rows, cols):
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))


def replay_rows(data: bytes, height: int = 24):
    s = Screen()
    s.feed(data.decode("utf-8", "replace"))
    rows = s.plain_lines()
    while len(rows) < height:
        rows.append("")
    return rows[:height]


def cell(rows, y, x):
    r = rows[y] if y < len(rows) else ""
    return r[x] if x < len(r) else " "


def max_line_num(rows) -> int:
    nums = []
    for r in rows:
        nums += [int(x) for x in re.findall(r"LINE-(\d{3})", r)]
    return max(nums) if nums else -1


def has_line(rows, name) -> bool:
    return any(name in r for r in rows)


def main() -> int:
    pid, master = pty.fork()
    if pid == 0:
        try:
            os.chdir("/tmp")
            env = os.environ.copy()
            env.update(TERM="xterm-256color", COLUMNS="80", LINES="24")
            os.execvpe(sys.executable, [sys.executable, "-c", CHILD], env)
        finally:
            os._exit(127)

    set_size(master, 24, 80)
    buf = bytearray()
    state = {"status": None}

    def pump(timeout=0.1):
        r, _, _ = select.select([master], [], [], timeout)
        if r:
            try:
                data = os.read(master, 65536)
            except OSError:
                data = b""
            if data:
                buf.extend(data)
                if b"\x1b[6n" in data:
                    try:
                        os.write(master, b"\x1b[24;1R")
                    except OSError:
                        pass
        if state["status"] is None:
            wpid, st = os.waitpid(pid, os.WNOHANG)
            if wpid == pid:
                state["status"] = st
                for _ in range(20):
                    pump(0.05)

    def send(seq, wait=0.35):
        try:
            os.write(master, seq)
        except OSError:
            pass
        time.sleep(wait)
        pump(0.25)

    start = time.time()
    # 等首帧（鼠标模式序列出现）
    while time.time() - start < 10 and b"\x1b[?1000h" not in buf:
        pump(0.2)
    time.sleep(0.4)
    pump(0.3)
    empty_rows = replay_rows(bytes(buf))
    empty_no_sb = all(cell(empty_rows, y, SB_X - 1) not in "█│"
                      for y in range(MAIN_H))

    # 等 200 行写完
    while time.time() - start < 15 and b"LINE-199" not in buf:
        pump(0.2)
    if b"LINE-199" not in buf:
        print("❌ 未见 LINE-199（主区写入失败）")
        return 1
    time.sleep(0.5)
    pump(0.3)
    full_rows = replay_rows(bytes(buf))
    sb_visible = any(cell(full_rows, y, SB_X - 1) == "█" for y in range(MAIN_H))
    thumb_bottom = cell(full_rows, MAIN_H - 1, SB_X - 1) == "█"

    # 拖拽 thumb：底(14) → 顶(0)
    send(b"\x1b[<0;%d;%dM" % (SB_X, MAIN_H), 0.25)        # down on thumb
    send(b"\x1b[<32;%d;1M" % SB_X, 0.25)                   # drag to top
    send(b"\x1b[<0;%d;1m" % SB_X, 0.5)                     # up
    drag_rows = replay_rows(bytes(buf))
    drag_top = has_line(drag_rows, "LINE-000") and not has_line(drag_rows, "LINE-199")
    thumb_top = cell(drag_rows, 0, SB_X - 1) == "█"

    # 点击轨道底部 → 跳回底部
    send(b"\x1b[<0;%d;%dM" % (SB_X, MAIN_H), 0.25)
    send(b"\x1b[<0;%d;%dm" % (SB_X, MAIN_H), 0.5)
    click_rows = replay_rows(bytes(buf))
    click_bottom = has_line(click_rows, "LINE-199")

    # 滚动条列滚轮向上 1 格（6 行）
    send(b"\x1b[<64;%d;5M" % SB_X, 0.6)
    wheel_rows = replay_rows(bytes(buf))
    wheel_num = max_line_num(wheel_rows)

    # 等子进程自然退出
    while state["status"] is None and time.time() - start < 40:
        pump(0.2)
    if state["status"] is None:
        try:
            os.kill(pid, signal.SIGKILL)
        except Exception:
            pass
        _, st = os.waitpid(pid, 0)
        state["status"] = st

    code = os.waitstatus_to_exitcode(state["status"])
    text_all = bytes(buf).decode("utf-8", "replace")

    print(f"退出码={code} 总输出={len(buf)}B")
    ok = True

    def check(name, cond):
        nonlocal ok
        print(("  ✅ " if cond else "  ❌ ") + name)
        ok = ok and cond

    check("空屏无滚动条", empty_no_sb)
    check("内容超屏后滚动条出现", sb_visible)
    check("初始 thumb 贴底", thumb_bottom)
    check("拖拽 thumb 到顶 → 视口到最旧内容", drag_top)
    check("拖拽后 thumb 在顶部", thumb_top)
    check("点击轨道底部 → 回到底部", click_bottom)
    check("滚动条列滚轮 6 行/格（max=194）", wheel_num == 194)
    check("无 Traceback", "Traceback" not in text_all)
    check("退出码 0", code == 0)

    print("\n" + ("✅ PASS" if ok else "❌ FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
