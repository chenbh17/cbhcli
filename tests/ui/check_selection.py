#!/usr/bin/env python3
"""应用内拖选 + Ctrl+C 复制门禁（v5.5.1 迭代）

用法：
    /usr/bin/python3 tests/ui/check_selection.py

覆盖：
    A. 单元：_extract_cols / _split_select 的列边界（含 CJK 宽字符不拆）
    B. pty 集成（24×80，HOME 隔离到临时目录）：
       1. 左键直接拖动（无需 Shift）选中部分行 → Ctrl+C = 复制：
          剪贴板桥文件内容正确、状态栏出现"已复制"、**不触发中断**
       2. 单击（无拖动）清除选区 → Ctrl+C = 中断（on_interrupt 被调用）
       3. 跨行拖动 → 复制多行文本（行尾空白去除）

SGR 鼠标序列：down=<0;x;yM  drag=<32;x;yM  up=<0;x;ym（x/y 1-based）
"""

from __future__ import annotations

import fcntl
import os
import pty
import select
import shutil
import signal
import struct
import sys
import termios
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from cbhcli_pkg.core.ui.lines import Screen  # noqa: E402
from cbhcli_pkg.core.ui.shell import _extract_cols, _split_select  # noqa: E402
from cbhcli_pkg.core.ui.ansi import DEFAULT_STYLE  # noqa: E402

FAKE_HOME = "/tmp/cbhcli_sel_test_home"

CHILD = r'''
import asyncio, sys
sys.path.insert(0, ROOT)
from cbhcli_pkg.core.ui.shell import FullScreenUI

def on_interrupt():
    print("INTERRUPTED-MARK")
    sys.stdout.flush()

async def main():
    ui = FullScreenUI(status_provider=lambda: [], on_interrupt=on_interrupt)
    ui.install_sink()
    task = asyncio.create_task(ui.app.run_async())
    await asyncio.sleep(0.8)
    for i in range(30):
        print(f"SEL-LINE-{i:02d}")
    sys.stdout.flush()
    ui.invalidate()
    await asyncio.sleep(16.0)
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


def unit_checks() -> bool:
    ok = True

    def check(name, cond):
        nonlocal ok
        print(("  ✅ " if cond else "  ❌ ") + name)
        ok = ok and cond

    s = DEFAULT_STYLE
    check("ASCII 列提取 [1,4)", _extract_cols([("ABCDEF", s)], 1, 4) == "BCD")
    check("CJK 宽字符不拆 [0,4)", _extract_cols([("你好world", s)], 0, 4) == "你好")
    check("CJK 跨界 [2,5)", _extract_cols([("你好world", s)], 2, 5) == "好w")
    parts = _split_select("ABCDEF", "", 1, 4)
    check("高亮拆分 reverse", parts == [("", "A"), (" reverse", "BCD"), ("", "EF")])
    parts2 = _split_select("你好world", "bold", 0, 4)
    check("CJK 高亮拆分", parts2 == [("bold reverse", "你好"), ("bold", "world")])
    return ok


def main() -> int:
    print("== 单元：列提取/高亮 ==")
    ok = unit_checks()

    shutil.rmtree(FAKE_HOME, ignore_errors=True)
    os.makedirs(FAKE_HOME, exist_ok=True)
    clip_path = os.path.join(FAKE_HOME, ".cbhcli", "clipboard.txt")

    pid, master = pty.fork()
    if pid == 0:
        try:
            os.chdir("/tmp")
            env = os.environ.copy()
            env.update(TERM="xterm-256color", COLUMNS="80", LINES="24",
                       HOME=FAKE_HOME)
            env.pop("DISPLAY", None)
            env.pop("WAYLAND_DISPLAY", None)
            # HOME 被隔离后 user-site（prompt_toolkit 所在）会失效，
            # 显式把真实 user-site 加进 PYTHONPATH
            import site
            env["PYTHONPATH"] = (site.getusersitepackages()
                                 + os.pathsep + env.get("PYTHONPATH", ""))
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

    def read_clip():
        try:
            with open(clip_path, "r", encoding="utf-8") as f:
                return f.read()
        except Exception:
            return None

    start = time.time()
    while time.time() - start < 15 and b"SEL-LINE-29" not in buf:
        pump(0.2)
    if b"SEL-LINE-29" not in buf:
        print("❌ 未见 SEL-LINE-29（主区写入失败）")
        open("/tmp/sel_pty_out.bin", "wb").write(bytes(buf))
        print("输出已存 /tmp/sel_pty_out.bin", len(buf))
        try:
            os.kill(pid, signal.SIGKILL)
            os.waitpid(pid, 0)
        except Exception:
            pass
        return 1
    time.sleep(0.5)
    pump(0.3)

    # 布局：main_h=15，total=31（30 行+尾空行），start=16
    # y=0 → SEL-LINE-16；"SEL-LINE-16" 共 11 字符（列 0..10）

    # --- 1. 单行部分选中：列 0..7 → "SEL-LINE"（8 字符）---
    send(b"\x1b[<0;1;1M", 0.25)     # down (0,0)
    send(b"\x1b[<32;8;1M", 0.25)    # drag → (7,0)
    send(b"\x1b[<0;8;1m", 0.4)      # up
    mark = len(buf)
    send(b"\x03", 0.6)              # Ctrl+C = 复制
    seg1 = bytes(buf[mark:])
    clip1 = read_clip()
    copied_notice = "已复制".encode("utf-8") in seg1
    no_interrupt1 = b"INTERRUPTED-MARK" not in seg1

    # --- 2. 单击清除选区 → Ctrl+C = 中断 ---
    send(b"\x1b[<0;1;1M", 0.2)
    send(b"\x1b[<0;1;1m", 0.3)
    mark = len(buf)
    send(b"\x03", 0.6)
    seg2 = bytes(buf[mark:])
    interrupted = b"INTERRUPTED-MARK" in seg2

    # --- 3. 跨行拖动：(0,0) → (11,2) = 3 个整行 ---
    # 注意：测试 2 的中断回调 print 了 INTERRUPTED-MARK（多一行），
    # 视口起点从 SEL-LINE-16 移到 SEL-LINE-17
    send(b"\x1b[<0;1;1M", 0.25)
    send(b"\x1b[<32;12;3M", 0.25)
    send(b"\x1b[<0;12;3m", 0.4)
    send(b"\x03", 0.6)
    clip3 = read_clip()
    expect3 = "SEL-LINE-17\nSEL-LINE-18\nSEL-LINE-19"

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

    print(f"\n== 集成：退出码={code} 总输出={len(buf)}B ==")
    print(f"clip1={clip1!r} clip3={clip3!r}")

    def check(name, cond):
        nonlocal ok
        print(("  ✅ " if cond else "  ❌ ") + name)
        ok = ok and cond

    check("拖动选中 Ctrl+C 复制内容正确", clip1 == "SEL-LINE")
    check("状态栏出现「已复制」提示", copied_notice)
    check("有选区时 Ctrl+C 不触发中断", no_interrupt1)
    check("单击清除选区后 Ctrl+C 触发中断", interrupted)
    check("跨行拖动复制多行文本", clip3 == expect3)
    check("无 Traceback", "Traceback" not in text_all)
    check("退出码 0", code == 0)

    shutil.rmtree(FAKE_HOME, ignore_errors=True)
    print("\n" + ("✅ PASS" if ok else "❌ FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
