#!/usr/bin/env python3
"""选区高亮位置门禁（v5.5.3 迭代：多片段行高亮重复/错位修复）

用法：
    /usr/bin/python3 tests/ui/check_selection_highlight.py

背景（用户实测 bug）：
    欢迎面板行 = 多个样式片段（dim 文本 run + 默认样式行尾补白空格 run）。
    旧 _main_fragments 把**整行**列范围直接传给每个片段的 _split_select
    （其内部 col 从 0 起算）→ 选区从每个片段开头重新套用：高亮重复、
    行尾补白空格被高亮成白色块（错位）。复制路径 _extract_cols 跨片段
    累计列号故复制正确——"复制对、显示错"。

⚠️ pyte.Screen 参数序是 **(columns, rows)**——写反成 (24, 80) 会变成
   24 列宽，第 24 列起折行，补白空格被折到下一行，看起来像"reverse
   丢失"（本门禁开发时实测踩坑）。正确 = pyte.Screen(80, 24)。
   补白高亮另用原始字节 SGR 检查交叉验证（reverse SGR 后紧跟空格）。

覆盖：
    A. 单元：_highlight_line 多片段列偏移（含 CJK 宽字符不拆、补白不高亮、
       片段完全在选区外原样保留、c0=None 透传）
    B. pty 集成（24×80，HOME 隔离）+ pyte 列精确重放：
       1. 单行拖选 dim 文本前 6 列 → reverse 格**恰好**落在列 0..5，
          行尾补白/其余列/相邻行均无 reverse（旧代码此处必红）
       2. Ctrl+C 复制内容正确（v5.5.3）
       3. 跨行拖选（styled 行 col2 → 下一行 col3）→ 两行 reverse 范围正确
       4. 第二次 Ctrl+C 复制多行内容正确

SGR 鼠标序列：down=<0;x;yM  drag=<32;x;yM  up=<0;x;ym（x/y 1-based）
"""

from __future__ import annotations

import fcntl
import os
import pty
import re
import select
import shutil
import signal
import struct
import sys
import termios
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from cbhcli_pkg.core.ui.shell import _highlight_line, _split_select  # noqa: E402
from cbhcli_pkg.core.ui.ansi import DEFAULT_STYLE, style_to_pt_style  # noqa: E402

FAKE_HOME = "/tmp/cbhcli_selhl_test_home"

CHILD = r'''
import asyncio, sys
sys.path.insert(0, ROOT)
from cbhcli_pkg.core.ui.shell import FullScreenUI

async def main():
    ui = FullScreenUI(status_provider=lambda: [], on_interrupt=lambda: None)
    ui.install_sink()
    task = asyncio.create_task(ui.app.run_async())
    await asyncio.sleep(0.8)
    for i in range(20):
        print(f"HL-LINE-{i:02d}")
    # 模拟欢迎面板行：dim 文本 run + 默认样式补白空格 run（两个片段）
    print("\x1b[2mv5.5.3 · AI 驱动的终端助手\x1b[0m" + " " * 20)
    for i in range(6):
        print(f"HL-AFTER-{i}")
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


def set_size(fd, rows, cols):
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))


def unit_checks() -> bool:
    ok = True

    def check(name, cond):
        nonlocal ok
        print(("  ✅ " if cond else "  ❌ ") + name)
        ok = ok and cond

    dim = DEFAULT_STYLE
    pad = DEFAULT_STYLE
    # 欢迎面板同构：dim 文本 run + 补白空格 run
    line = [("v5.5.3 · AI 驱动的终端助手", dim), (" " * 20, pad)]
    got = _highlight_line(line, 0, 6)
    want = [(style_to_pt_style(dim) + " reverse", "v5.5.3"),
            (style_to_pt_style(dim), " · AI 驱动的终端助手"),
            (style_to_pt_style(pad), " " * 20)]
    check("多片段：高亮只落在选区列（补白不高亮）", got == want)

    # 旧 bug 复现对照：整行范围直传每个片段 → 补白前 6 列被 reverse
    bad = []
    for text, style in line:
        bad.extend(_split_select(text, style_to_pt_style(style), 0, 6))
    check("对照：旧逻辑确实会高亮补白空格（bug 存在过）",
          any("reverse" in s and t.strip() == "" for s, t in bad))

    # CJK 宽字符不拆：列 9..13 = "AI 驱"（驱占 12-13，不切半）
    got2 = _highlight_line(line, 9, 13)
    rev2 = [t for s, t in got2 if "reverse" in s]
    check("多片段 CJK 边界不拆宽字符", rev2 == ["AI 驱"])

    # 三个等宽片段：选区 [1,5) 跨片段累计
    ps = style_to_pt_style(DEFAULT_STYLE)
    line3 = [("AB", DEFAULT_STYLE), ("CD", DEFAULT_STYLE), ("EF", DEFAULT_STYLE)]
    got3 = _highlight_line(line3, 1, 5)
    check("跨片段列偏移累计", got3 == [
        (ps, "A"), (ps + " reverse", "B"),
        (ps + " reverse", "CD"),
        (ps + " reverse", "E"), (ps, "F")])

    # 片段完全在选区外 → 原样（不产生空 reverse 片段）
    got4 = _highlight_line(line3, 4, 6)
    check("选区外片段原样保留", got4 == [
        (ps, "AB"), (ps, "CD"), (ps + " reverse", "EF")])

    # c0=None 透传
    got5 = _highlight_line(line3, None, None)
    check("无选区透传", got5 == [(ps, "AB"), (ps, "CD"), (ps, "EF")])
    return ok


def main() -> int:
    print("== 单元：多片段高亮列偏移 ==")
    ok = unit_checks()

    import pyte  # 列精确重放（Screen 重放对宽字符/光标列不可靠）

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

    def replay(data):
        screen = pyte.Screen(80, 24)   # ⚠️ 参数序 = (columns, rows)
        stream = pyte.Stream(screen)
        stream.feed(data.decode("utf-8", "replace"))
        return screen

    def row_of(screen, needle):
        for r in range(24):
            text = "".join(screen.buffer[r][c].data for c in range(80))
            if needle in text:
                return r
        return None

    def rev_cols(screen, r):
        return [c for c in range(80) if screen.buffer[r][c].reverse]

    start = time.time()
    while time.time() - start < 15 and b"HL-AFTER-5" not in buf:
        pump(0.2)
    if b"HL-AFTER-5" not in buf:
        print("❌ 未见 HL-AFTER-5（主区写入失败）")
        open("/tmp/selhl_pty_out.bin", "wb").write(bytes(buf))
        try:
            os.kill(pid, signal.SIGKILL)
            os.waitpid(pid, 0)
        except Exception:
            pass
        return 1
    time.sleep(0.5)
    pump(0.3)

    # 布局：27 行打印 + 尾空行 = 28；main_h=15 → start=13
    # styled 行 idx=20 → y=7；HL-AFTER-0 idx=21 → y=8
    # --- 1. 单行拖选列 0..5（"v5.5.3"）---
    send(b"\x1b[<0;1;8M", 0.25)
    send(b"\x1b[<32;6;8M", 0.25)
    send(b"\x1b[<0;6;8m", 0.45)
    mark1 = len(buf)
    scr1 = replay(bytes(buf[:mark1]))
    r1 = row_of(scr1, "v5.5.3")
    rev1 = rev_cols(scr1, r1) if r1 is not None else None
    above = rev_cols(scr1, r1 - 1) if r1 else None
    below = rev_cols(scr1, r1 + 1) if r1 is not None else None
    # 原始字节交叉验证：reverse SGR 后紧跟空格 = 补白被高亮（旧 bug 症状）
    pad_rev1 = re.search(rb"\x1b\[[0-9;]*7m +", bytes(buf[:mark1])) is not None
    send(b"\x03", 0.6)
    clip1 = read_clip()

    # --- 2. 跨行拖选：styled 行 col2 → HL-AFTER-0 col3 ---
    send(b"\x1b[<0;3;8M", 0.25)
    send(b"\x1b[<32;4;9M", 0.25)
    send(b"\x1b[<0;4;9m", 0.45)
    mark2 = len(buf)
    if os.environ.get("SELHL_DUMP"):
        open("/tmp/selhl_pty_out.bin", "wb").write(bytes(buf))
    scr2 = replay(bytes(buf[:mark2]))
    r2 = row_of(scr2, "v5.5.3")
    r2b = row_of(scr2, "HL-AFTER-0")
    rev2a = rev_cols(scr2, r2) if r2 is not None else None
    rev2b = rev_cols(scr2, r2b) if r2b is not None else None
    # 跨行首行选到行尾：补白空格应被 reverse（原生终端选区语义）；
    # 原始字节交叉验证 reverse SGR + 连续空格确实写出
    pad_rev2 = re.search(rb"\x1b\[0;7m {10,}", bytes(buf[mark1:mark2])) is not None
    send(b"\x03", 0.6)
    clip2 = read_clip()

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
    print(f"rev1={rev1} rev2a={rev2a} rev2b={rev2b}")
    print(f"clip1={clip1!r} clip2={clip2!r}")

    def check(name, cond):
        nonlocal ok
        print(("  ✅ " if cond else "  ❌ ") + name)
        ok = ok and cond

    check("单行选区 reverse 恰好列 0..5", rev1 == list(range(6)))
    check("单行：补白空格未被高亮（无行尾白块）", not pad_rev1)
    check("上一行无 reverse（无重复高亮）", above == [])
    check("下一行无 reverse（无重复高亮）", below == [])
    check("Ctrl+C 复制 v5.5.3", clip1 == "v5.5.3")
    # 行宽 = 文本 26 列（v5.5.3=6 + " · "=3 + AI=2 + 空格1 + 7×CJK2=14）+ 补白 20 = 46
    check("跨行：首行 reverse 列 2..45（文本+补白到行尾）",
          rev2a == list(range(2, 46)))
    check("跨行：首行补白随选区到行尾 reverse", pad_rev2)
    check("跨行：末行 reverse 列 0..3", rev2b == [0, 1, 2, 3])
    check("跨行复制内容正确",
          clip2 == ".5.3 · AI 驱动的终端助手\nHL-A")
    check("无 Traceback", "Traceback" not in text_all)
    check("退出码 0", code == 0)

    shutil.rmtree(FAKE_HOME, ignore_errors=True)
    print("\n" + ("✅ PASS" if ok else "❌ FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
