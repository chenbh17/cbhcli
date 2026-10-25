#!/usr/bin/env python3
"""「运行中」标识门禁（v5.5.0 补充）

用法：
    /usr/bin/python3 tests/ui/check_running_badge.py

流程：子进程启动真实 FullScreenUI（状态栏带锚点 marker）
  t≈0.4s 打印若干行 → t≈1.2s `set_busy(True)` → t≈3.2s `set_busy(False)` → 退出
父进程分别在"运行中"和"空闲"两个时间窗重放屏幕，断言：
  - 运行中：状态栏出现 `<spinner> 运行中`，且 spinner 帧随时间变化（动画）
  - 空闲：状态栏不再出现
  - 主区行数不因标识变化而增减（标识只在状态栏）
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
    ui = FullScreenUI(status_provider=lambda: [("", "statusbar-marker")])
    ui.install_sink()
    task = asyncio.create_task(ui.app.run_async())
    await asyncio.sleep(0.4)
    for i in range(12):
        print(f"LINE-{i:03d}")
    sys.stdout.flush()
    await asyncio.sleep(0.8)
    ui.set_busy(True)      # t≈1.2s：应出现「运行中」
    await asyncio.sleep(2.0)
    ui.set_busy(False)     # t≈3.2s：应消失
    await asyncio.sleep(1.5)
    ui.close_input()
    ui.app.exit()
    try:
        await asyncio.wait_for(task, timeout=5)
    except Exception:
        pass
    ui.uninstall_sink()

asyncio.run(main())
'''.replace("ROOT", repr(ROOT))


def set_size(fd, rows, cols):
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))


def rows_of(data: bytes):
    s = Screen()
    s.feed(data.decode("utf-8", "replace"))
    return [r.strip() for r in s.plain_lines() if r.strip()]


def main() -> int:
    pid, m = pty.fork()
    if pid == 0:
        try:
            os.chdir("/tmp")
            env = os.environ.copy()
            env.update(TERM="xterm-256color", COLUMNS="80", LINES="24")
            os.execvpe(sys.executable, [sys.executable, "-c", CHILD], env)
        finally:
            os._exit(127)

    set_size(m, 24, 80)
    buf = bytearray()
    st = {"status": None}

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
                        os.write(m, b"\x1b[24;1R")
                    except OSError:
                        pass
        if st["status"] is None:
            w, status = os.waitpid(pid, os.WNOHANG)
            if w == pid:
                st["status"] = status
                for _ in range(20):
                    pump(0.05)

    start = time.time()
    while time.time() - start < 10 and b"LINE-011" not in buf:
        pump(0.15)

    # 运行中：连续采样，收集 spinner 帧（验证"在动"）
    while time.time() - start < 1.35:
        pump(0.05)
    spin_seen = []
    busy_rows = []
    while time.time() - start < 3.0:
        pump(0.08)
        rows = rows_of(bytes(buf))
        hits = [r.strip() for r in rows if "运行中" in r]
        if hits:
            busy_rows = rows
            ch = hits[0][0] if hits[0] else ""
            if ch and (not spin_seen or spin_seen[-1] != ch):
                spin_seen.append(ch)

    # 已恢复空闲
    while time.time() - start < 4.6:
        pump(0.1)
    idle_rows = rows_of(bytes(buf))

    while st["status"] is None and time.time() - start < 20:
        pump(0.2)
    if st["status"] is None:
        try:
            os.kill(pid, signal.SIGKILL)
        except Exception:
            pass
        _, status = os.waitpid(pid, 0)
        st["status"] = status
    try:
        os.close(m)
    except OSError:
        pass

    code = os.waitstatus_to_exitcode(st["status"])
    text = bytes(buf).decode("utf-8", "replace")

    busy_status = "\n".join(busy_rows[-3:])
    idle_status = "\n".join(idle_rows[-3:])

    print(f"退出码={code}")
    print(f"运行中状态栏: {busy_status[:100]!r}")
    print(f"空闲状态栏: {idle_status[:100]!r}")

    ok = True

    def check(name, cond):
        nonlocal ok
        print(("  ✅ " if cond else "  ❌ ") + name)
        ok = ok and cond

    frames = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
    spin_chars = [c for c in spin_seen if c in frames]
    check("运行中显示 spinner 帧 + 运行中",
          bool(spin_chars) and "运行中" in busy_status)
    check("spinner 在动（观察到 >=3 个不同帧）", len(set(spin_chars)) >= 3)
    check("不再使用圆圈标识", "● 运行中" not in busy_status)
    check("运行中标识在状态栏（含 marker 的同一区域）",
          "statusbar-marker" in busy_status)
    check("空闲后标识消失", "运行中" not in idle_status)
    check("主区行数不变（标识只在状态栏）",
          sum(1 for r in busy_rows if r.startswith("LINE-")) ==
          sum(1 for r in idle_rows if r.startswith("LINE-")))
    check("无 Traceback", "Traceback" not in text)
    check("退出码 0", code == 0)

    print("\n" + ("✅ PASS" if ok else "❌ FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
