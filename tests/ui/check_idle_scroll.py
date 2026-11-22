#!/usr/bin/env python3
"""空闲自动回底计时门禁（v5.5.1 迭代：任何交互重置 + 2 分钟 + 竞态修复）

用法：
    /usr/bin/python3 tests/ui/check_idle_scroll.py

覆盖：
    A. 单元：
       - 常量 = 120 秒
       - after_key_press 已挂接活动重置钩子
       - 代际号竞态修复：两次快速 arm，第一个定时器不得提前触发
         （旧实现 cancel 发生在 call_soon_threadsafe 的 _schedule 之前，
          两个定时器并存 → "只按第一次滚动计时"）
    B. pty 集成（子进程把计时 patch 成 1s 便于测试）：
       滚轮上滚 → 0.6s 后按键 → 再 0.6s（距首次滚动 1.2s > 1s）
       必须仍停在历史位置（按键重置了计时）→ 再等 1.3s 自动回底。
"""

from __future__ import annotations

import asyncio
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

from cbhcli_pkg.core.ui import shell as shell_mod  # noqa: E402
from cbhcli_pkg.core.ui.shell import FullScreenUI  # noqa: E402
from cbhcli_pkg.core.ui.lines import Screen  # noqa: E402

CHILD = r'''
import asyncio, sys
sys.path.insert(0, ROOT)
from cbhcli_pkg.core.ui import shell as shell_mod
shell_mod._IDLE_SCROLL_RESET_SEC = 1.0   # 测试用短计时
from cbhcli_pkg.core.ui.shell import FullScreenUI

async def main():
    ui = FullScreenUI(status_provider=lambda: [])
    ui.install_sink()
    task = asyncio.create_task(ui.app.run_async())
    await asyncio.sleep(0.5)
    for i in range(200):
        print(f"LINE-{i:03d}")
    sys.stdout.flush()
    ui.invalidate()
    await asyncio.sleep(10.0)
    ui.close_input()
    ui.app.exit()
    try:
        await asyncio.wait_for(task, timeout=5)
    except Exception:
        pass
    ui.uninstall_sink()

asyncio.run(main())
'''.replace("ROOT", repr(ROOT))


def unit_checks() -> bool:
    ok = True

    def check(name, cond):
        nonlocal ok
        print(("  ✅ " if cond else "  ❌ ") + name)
        ok = ok and cond

    check("空闲回底常量 = 120 秒", shell_mod._IDLE_SCROLL_RESET_SEC == 120.0)

    ui = FullScreenUI(status_provider=lambda: [])
    try:
        handlers = ui.app.key_processor.after_key_press._handlers
        hooked = any("_note_user_activity" in repr(h) for h in handlers)
    except Exception:
        hooked = False
    check("after_key_press 挂接活动重置钩子", hooked)

    async def race():
        loop = asyncio.get_running_loop()
        ui.app.loop = loop
        ui.invalidate = lambda *a, **k: None   # 只测定时，不测渲染
        ui._offset = 5
        old = shell_mod._IDLE_SCROLL_RESET_SEC
        shell_mod._IDLE_SCROLL_RESET_SEC = 0.3
        try:
            ui._arm_idle_scroll_reset()
            await asyncio.sleep(0.2)
            ui._arm_idle_scroll_reset()     # 第二次交互重置计时
            await asyncio.sleep(0.25)       # 距第一次 0.45s > 0.3s
            still = ui._offset == 5         # 第一个定时器不得触发
            await asyncio.sleep(0.2)        # 距第二次 0.45s > 0.3s
            fired = ui._offset == 0
        finally:
            shell_mod._IDLE_SCROLL_RESET_SEC = old
        return still, fired

    still, fired = asyncio.run(race())
    check("竞态修复：快速二次 arm 后旧定时器不提前触发", still)
    check("新定时器到时正常回底", fired)
    return ok


def set_size(fd, rows, cols):
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))


def visible_has(data: bytes, name: str) -> bool:
    s = Screen()
    s.feed(data.decode("utf-8", "replace"))
    rows = [r.strip() for r in s.plain_lines() if r.strip()]
    return any(name in r for r in rows[-24:])


def pty_check() -> bool:
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

    def send(seq, wait=0.3):
        try:
            os.write(master, seq)
        except OSError:
            pass
        time.sleep(wait)
        pump(0.2)

    start = time.time()
    while time.time() - start < 15 and b"LINE-199" not in buf:
        pump(0.2)
    if b"LINE-199" not in buf:
        print("❌ 未见 LINE-199（主区写入失败）")
        return False
    time.sleep(0.4)
    pump(0.3)

    send(b"\x1b[<64;5;5M", 0.15)   # 滚轮上 ×2（12 行）
    send(b"\x1b[<64;5;5M", 0.15)
    scrolled = not visible_has(bytes(buf), "LINE-199")

    time.sleep(0.6)
    send(b"x", 0.1)                # 按键 → 应重置计时
    time.sleep(0.6)                # 距首次滚动 ~1.2s > 1s
    pump(0.3)
    kept = not visible_has(bytes(buf), "LINE-199")

    time.sleep(1.3)                # 距按键 ~1.4s > 1s → 自动回底
    pump(0.4)
    returned = visible_has(bytes(buf), "LINE-199")

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

    ok = True

    def check(name, cond):
        nonlocal ok
        print(("  ✅ " if cond else "  ❌ ") + name)
        ok = ok and cond

    check("滚轮上滚离开底部", scrolled)
    check("按键重置计时（1.2s 后仍停在历史位置）", kept)
    check("无操作 1s+ 后自动回底", returned)
    check("无 Traceback", "Traceback" not in text_all)
    check("退出码 0", code == 0)
    return ok


def main() -> int:
    print("== 单元：常量/钩子/竞态 ==")
    ok = unit_checks()
    print("\n== 集成：按键重置计时 ==")
    ok = pty_check() and ok
    print("\n" + ("✅ PASS" if ok else "❌ FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
