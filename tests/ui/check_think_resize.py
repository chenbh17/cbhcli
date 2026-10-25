#!/usr/bin/env python3
"""思考窗口 resize 重复门禁（v5.5.0 修复）

用法：
    /usr/bin/python3 tests/ui/check_think_resize.py

覆盖两个层面：

A. 逻辑缓冲层（组件级）
   ScreenSink 已安装时，思考窗口在**宽度反复变化**下必须：
     - 只保留一个窗口（1 个 `💭` 头行）
     - 内容不重复（每个 chunk 标记只出现 1 次）
   （旧实现每次收窄都放弃擦除、把上一窗口留成快照 -> 重复）

B. 物理屏幕层（真实 Application + pty resize）
   流式思考过程中连续 resize（100x30 → 40x12 → 30x10 → 100x30），
   重放终端输出后，可见区域内 `💭` 只有 1 个、标记无重复。

C. legacy 兜底：未安装 ScreenSink（内联模式）时仍保留旧"快照"策略，
   确认没有被本次修复误改（`\\033[9A` 不应在收窄时出现）。
"""

from __future__ import annotations

import fcntl
import io
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

from cbhcli_pkg.core.thinking_display import ThinkingDisplay  # noqa: E402
from cbhcli_pkg.core.ui.lines import Screen  # noqa: E402
from cbhcli_pkg.core.ui.shell import FullScreenUI  # noqa: E402

SENT = "甲" * 12          # 24 显示列
CHUNKS = 17


# ----------------------------------------------------------------------
#  A. 逻辑缓冲层
# ----------------------------------------------------------------------
def check_buffer() -> bool:
    real = sys.stdout
    ui = FullScreenUI(status_provider=lambda: [])
    ui.install_sink()
    td = ThinkingDisplay(max_lines=8)
    try:
        widths = [100] * 5 + [80] * 3 + [60] * 3 + [40] * 3 + [100] * 3
        td.start_thinking()
        for i, w in enumerate(widths):
            td._get_term_width = lambda w=w: w
            td.add_content(SENT + f"#{i}\n")
        td.finish_thinking()
        plain = ui.screen.plain_lines()
    finally:
        ui.uninstall_sink()

    text = "\n".join(plain)
    headers = [ln for ln in plain if "💭" in ln]
    marks = re.findall(r"#(\d+)", text)
    dup = {m: marks.count(m) for m in dict.fromkeys(marks) if marks.count(m) > 1}

    print(f"[A] 逻辑行={len(plain)} 窗口数={len(headers)} 标记={len(marks)} 重复={dup}")
    ok = True

    def check(name, cond):
        nonlocal ok
        print(("  ✅ " if cond else "  ❌ ") + name)
        ok = ok and cond

    check("只有一个思考窗口（1 个 💭）", len(headers) == 1)
    check("无重复内容标记", not dup)
    check("最后窗口保留 8 行", sum(1 for ln in plain if ln.startswith("甲")) == 8)
    return ok


# ----------------------------------------------------------------------
#  C. legacy（未安装 ScreenSink）行为不变
# ----------------------------------------------------------------------
def check_legacy() -> bool:
    real = sys.stdout
    buf = io.StringIO()
    sys.stdout = buf
    try:
        td = ThinkingDisplay(max_lines=8)
        td._get_term_width = lambda: 100
        td.start_thinking()
        td.add_content(SENT + "#0\n")
        td._get_term_width = lambda: 60          # 收窄
        td.add_content(SENT + "#1\n")
        td.finish_thinking()
        out = buf.getvalue()
    finally:
        sys.stdout = real

    snapshot = "\033[9A" not in out
    headers = out.count("💭")
    print(f"[C] legacy 输出 {len(out)}B，💭 次数={headers}，收窄未擦除={snapshot}")
    ok = True
    if not snapshot:
        print("  ❌ legacy 收窄时不应上移擦除（会误删上方内容）")
        ok = False
    else:
        print("  ✅ legacy 收窄仍走快照策略")
    return ok


# ----------------------------------------------------------------------
#  B. 物理屏幕层
# ----------------------------------------------------------------------
CHILD = r'''
import asyncio, sys
sys.path.insert(0, ROOT)
from cbhcli_pkg.core.ui.shell import FullScreenUI
from cbhcli_pkg.core.thinking_display import ThinkingDisplay

async def main():
    ui = FullScreenUI(status_provider=lambda: [])
    ui.install_sink()
    td = ThinkingDisplay(max_lines=8)
    task = asyncio.create_task(ui.app.run_async())
    await asyncio.sleep(0.4)

    def stream():
        td.start_thinking()
        for i in range(CHUNKS):
            td.add_content("甲" * 12 + f"#{i}\n")
            import time as _t
            _t.sleep(0.18)
        td.finish_thinking()

    await asyncio.get_running_loop().run_in_executor(None, stream)
    await asyncio.sleep(1.2)
    ui.close_input()
    ui.app.exit()
    try:
        await asyncio.wait_for(task, timeout=5)
    except Exception:
        pass
    ui.uninstall_sink()

asyncio.run(main())
'''.replace("ROOT", repr(ROOT)).replace("CHUNKS", str(CHUNKS))


def check_screen() -> bool:
    pid, master = pty.fork()
    if pid == 0:
        try:
            os.chdir("/tmp")
            env = os.environ.copy()
            env.update(TERM="xterm-256color", COLUMNS="100", LINES="30")
            os.execvpe(sys.executable, [sys.executable, "-c", CHILD], env)
        finally:
            os._exit(127)

    def set_size(rows, cols):
        fcntl.ioctl(master, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))

    set_size(30, 100)
    buf = bytearray()
    status = {"st": None}

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
                        os.write(master, b"\x1b[30;1R")
                    except OSError:
                        pass
        if status["st"] is None:
            wpid, st = os.waitpid(pid, os.WNOHANG)
            if wpid == pid:
                status["st"] = st
                for _ in range(20):
                    pump(0.05)

    start = time.time()
    # 等第一个窗口出现
    while time.time() - start < 12 and "💭".encode() not in buf:
        pump(0.2)

    plan = [(1.4, 14, 60), (1.4, 10, 40), (1.4, 30, 100), (1.0, 12, 30),
            (0.8, 8, 20), (0.8, 6, 15)]
    for delay, rows, cols in plan:
        time.sleep(delay)
        set_size(rows, cols)
        try:
            os.kill(pid, signal.SIGWINCH)
        except OSError:
            pass
        pump(0.3)

    while status["st"] is None and time.time() - start < 30:
        pump(0.2)
    if status["st"] is None:
        try:
            os.kill(pid, signal.SIGKILL)
        except Exception:
            pass
        _, st = os.waitpid(pid, 0)
        status["st"] = st

    code = os.waitstatus_to_exitcode(status["st"])
    text_all = bytes(buf).decode("utf-8", "replace")

    # 用最终宽度重放（宽度影响折行，因此按最后一屏 30 列重放最后一段输出）
    s = Screen()
    s.feed(text_all)
    rows = [r.strip() for r in s.plain_lines() if r.strip()]
    visible = rows[-25:]
    joined = "\n".join(visible)
    heads = joined.count("💭")
    marks = re.findall(r"#(\d+)", joined)
    dup = {m: marks.count(m) for m in dict.fromkeys(marks) if marks.count(m) > 1}

    print(f"[B] 退出码={code} 可见行={len(visible)} 窗口数={heads} 标记={len(marks)} 重复={dup}")
    ok = True

    def check(name, cond):
        nonlocal ok
        print(("  ✅ " if cond else "  ❌ ") + name)
        ok = ok and cond

    check("无 Traceback", "Traceback" not in text_all)
    check("退出码 0", code == 0)
    check("屏幕可见只有 1 个思考窗口", heads == 1)
    check("屏幕可见无重复标记", not dup)
    return ok


def main() -> int:
    ok = check_buffer()
    ok = check_legacy() and ok
    ok = check_screen() and ok
    print("\n" + ("✅ PASS" if ok else "❌ FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
