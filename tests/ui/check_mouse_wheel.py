#!/usr/bin/env python3
"""鼠标滚轮滚动门禁（v5.5.0 补充）

用法：
    /usr/bin/python3 tests/ui/check_mouse_wheel.py

流程：
    1. 子进程启动真实 `FullScreenUI`（full_screen=True，mouse_support=True）
    2. 主区写入 200 行标记 LINE-000..LINE-199（超过一屏）
    3. 父进程（pty 驱动）依次发送：
         - SGR 滚轮向上 ×5（每次 3 行）→ 应看到更早的行
         - SGR 滚轮向下 ×6 → 应回到贴底
         - 按 F2（已取消该开关）→ 鼠标模式不变，滚轮仍有效
    4. 断言：
         - 全屏输出含 `\\033[?1000h`（鼠标上报已开启）
         - 上滚后可见行号下移（min < 175，且看不到 LINE-199）
         - 下滚后重新看到 LINE-199
         - 无 Traceback，退出码 0

说明：prompt_toolkit 是**单元格级差分重绘**，原始字节里不会出现整行文本，
因此这里用 `core.ui.lines.Screen` 重放终端输出，再断言"重构后的屏幕内容"。
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
    await asyncio.sleep(0.4)
    for i in range(200):
        print(f"LINE-{i:03d}")
    sys.stdout.flush()
    ui.invalidate()
    await asyncio.sleep(8.0)   # 等父进程：滚轮上/下 + F2 + 再滚轮
    ui.close_input()
    ui.app.exit()
    try:
        await asyncio.wait_for(task, timeout=5)
    except Exception:
        pass
    ui.uninstall_sink()

asyncio.run(main())
'''.replace("ROOT", repr(ROOT))

WHEEL_UP = b"\x1b[<64;5;5M"
WHEEL_DOWN = b"\x1b[<65;5;5M"
F2 = b"\x1bOQ"


def set_size(fd, rows, cols):
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))


def visible_lines(data: bytes, height: int = 24):
    """重放终端输出，返回最后 height 个非空行（去首尾空白）"""
    s = Screen()
    s.feed(data.decode("utf-8", "replace"))
    rows = [r.strip() for r in s.plain_lines() if r.strip()]
    return rows[-height:]


def next_line_num(rows) -> int:
    """屏上最大的 LINE-nnn 编号（看不到任何 LINE 时返回 -1）"""
    import re
    nums = []
    for r in rows:
        nums += [int(x) for x in re.findall(r"LINE-(\d{3})", r)]
    return max(nums) if nums else -1


def main() -> int:
    pid, master = pty.fork()
    if pid == 0:
        try:
            os.chdir("/tmp")
            env = os.environ.copy()
            env.update(TERM="xterm-256color", COLUMNS="80", LINES="24",
                       CBHCLI_MOUSE="on")
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

    start = time.time()
    while time.time() - start < 15 and b"LINE-199" not in buf:
        pump(0.2)
    if b"LINE-199" not in buf:
        print("❌ 未见 LINE-199（主区写入失败）")
        return 1
    time.sleep(0.5)
    pump(0.3)

    mouse_on = b"\x1b[?1000h" in buf
    # xterm.js（JupyterLab 终端）把 DECRST 1003 实现为"整个鼠标协议=NONE"，
    # 所以关 1003 之后必须重发 1000h/1006h，否则滚轮彻底失效。
    first_off = bytes(buf).find(b"\x1b[?1003l")
    reenabled = first_off != -1 and b"\x1b[?1000h" in bytes(buf)[first_off:]
    initial = visible_lines(bytes(buf))
    initial_num = next_line_num(initial)

    def wheel(seq, times):
        for _ in range(times):
            try:
                os.write(master, seq)
            except OSError:
                pass
            time.sleep(0.15)
        time.sleep(0.9)
        pump(0.5)
        return visible_lines(bytes(buf))

    up_rows = wheel(WHEEL_UP, 5)
    up_num = next_line_num(up_rows)
    up_has_last = any("LINE-199" in r for r in up_rows)

    down_rows = wheel(WHEEL_DOWN, 6)
    down_num = next_line_num(down_rows)
    down_has_last = any("LINE-199" in r for r in down_rows)

    # F2 已取消：不应关闭鼠标上报，滚轮仍应有效
    f2_mark = len(buf)
    try:
        os.write(master, F2)
    except OSError:
        pass
    time.sleep(0.8)
    pump(0.5)
    f2_seg = bytes(buf[f2_mark:])
    f2_kept_mouse = b"\x1b[?1000l" not in f2_seg
    before = visible_lines(bytes(buf))
    after_f2 = wheel(WHEEL_UP, 3)
    moved_after_f2 = next_line_num(after_f2) < next_line_num(before)

    # 等子进程自然退出（子进程 sleep 8s 后自行 exit）
    while state["status"] is None and time.time() - start < 30:
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
    print(f"初始可见最大行号={initial_num}")
    print(f"上滚后可见最大行号={up_num}（含 LINE-199={up_has_last}）")
    print(f"下滚后可见最大行号={down_num}（含 LINE-199={down_has_last}）")
    print(f"F2 未关闭鼠标={f2_kept_mouse}，F2 后滚轮仍可上滚={moved_after_f2}")

    ok = True

    def check(name, cond):
        nonlocal ok
        print(("  ✅ " if cond else "  ❌ ") + name)
        ok = ok and cond

    check("鼠标上报已开启（\\033[?1000h）", mouse_on)
    check("关 1003 后重发 1000h（xterm.js 兼容）", reenabled)
    check("初始贴底（可见 LINE-199）", initial_num == 199)
    check("上滚看到更早内容（max < 199 且明显上移）",
          up_num != -1 and up_num < 199 and not up_has_last)
    check("上滚幅度合理（max <= 186）", up_num != -1 and up_num <= 186)
    check("下滚回到贴底（可见 LINE-199）", down_has_last)
    check("F2 不再关闭鼠标捕获", f2_kept_mouse)
    check("F2 后滚轮仍可滚动", moved_after_f2)
    check("无 Traceback", "Traceback" not in text_all)
    check("退出码 0", code == 0)

    print("\n" + ("✅ PASS" if ok else "❌ FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
