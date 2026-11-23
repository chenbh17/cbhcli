#!/usr/bin/env python3
"""工具确认界面按行输出门禁（v5.5.4 迭代）

用法：
    /usr/bin/python3 tests/ui/check_confirm_lines.py

背景（用户反馈）：旧版确认提示把 [Y/n/all/always] 挤在底部输入行的
一行提示里，与底部状态栏/输入区之间没有空行、不明显。现改为选项按行
输出到主区（每行一个选项 + 说明），选项块末尾留一空行与底部输入行隔开。

覆盖：
    A. 全屏（pty 24×80 + pyte 列精确重放）：
       1. 提示行上方不空行（always 行紧贴模态行）、下方空两行
          （spacer 与状态栏隔开）、提示行黄色
       2. 模态提示为短提示「选择 [y/n/all/always]:」（内联一行）
          + braille 待选择动画（≥2 帧）
       3. 输入 y 回车 → 确认返回 True；第二轮输入 n → 返回 False
    B. legacy（无全屏 UI，纯 stdout + pyte 重放）：
       选项逐行、提示行上方不空行、下方空两行 + 黄色待选择动画
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

FAKE_HOME = "/tmp/cbhcli_confirm_test_home"

CHILD_FS = r'''
import asyncio, sys, threading, types
sys.path.insert(0, ROOT)
from cbhcli_pkg.core.ui.shell import FullScreenUI
from cbhcli_pkg.core import prompt_utils
from cbhcli_pkg.core.tool_executor import ToolExecutor

async def main():
    ui = FullScreenUI(status_provider=lambda: [])
    ui.install_sink()
    prompt_utils.set_modal_hook(ui.ask_text_sync)
    task = asyncio.create_task(ui.app.run_async())
    await asyncio.sleep(0.8)
    for i in range(30):
        print(f"FILL-LINE-{i:02d}")
    sys.stdout.flush()
    ui.invalidate()
    await asyncio.sleep(0.5)

    dummy = types.SimpleNamespace(_confirm_callback=None,
                                  no_more_confirmations=False,
                                  permission_engine=None)
    out = {}

    def work(tag):
        out[tag] = ToolExecutor._confirm_execution(
            dummy, "terminal", {"command": "echo hi"})

    t = threading.Thread(target=work, args=("r1",), daemon=True)
    t.start()
    for _ in range(300):
        await asyncio.sleep(0.1)
        if "r1" in out:
            break
    print(f"CONFIRM-RESULT-1={out.get('r1')}")
    sys.stdout.flush()
    ui.invalidate()
    await asyncio.sleep(0.6)

    # 阶段标记写文件（渲染字节匹配不可靠，见 check_undo_redo 教训）
    with open("/tmp/cbhcli_confirm_phase.txt", "w") as f:
        f.write("2")
    t2 = threading.Thread(target=work, args=("r2",), daemon=True)
    t2.start()
    for _ in range(300):
        await asyncio.sleep(0.1)
        if "r2" in out:
            break
    print(f"CONFIRM-RESULT-2={out.get('r2')}")
    sys.stdout.flush()
    ui.invalidate()
    await asyncio.sleep(0.8)
    ui.close_input()
    ui.app.exit()
    try:
        await asyncio.wait_for(task, timeout=5)
    except Exception:
        pass
    prompt_utils.set_modal_hook(None)
    ui.uninstall_sink()

asyncio.run(main())
'''.replace("ROOT", repr(ROOT))

CHILD_LEGACY = r'''
import sys, threading, types
sys.path.insert(0, ROOT)
from cbhcli_pkg.core.tool_executor import ToolExecutor

dummy = types.SimpleNamespace(_confirm_callback=None,
                              no_more_confirmations=False,
                              permission_engine=None)
out = {}

def work():
    out["r"] = ToolExecutor._confirm_execution(
        dummy, "terminal", {"command": "echo hi"})

t = threading.Thread(target=work, daemon=True)
t.start()
t.join(30)
print(f"CONFIRM-RESULT-1={out.get('r')}")
sys.stdout.flush()
'''.replace("ROOT", repr(ROOT))


def set_size(fd, rows, cols):
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))


class Runner:
    def __init__(self, child: str):
        self.pid, self.master = pty.fork()
        if self.pid == 0:
            try:
                os.chdir("/tmp")
                env = os.environ.copy()
                env.update(TERM="xterm-256color", COLUMNS="80", LINES="24",
                           HOME=FAKE_HOME)
                env.pop("DISPLAY", None)
                env.pop("WAYLAND_DISPLAY", None)
                import site
                env["PYTHONPATH"] = (ROOT + os.pathsep
                                     + site.getusersitepackages()
                                     + os.pathsep + env.get("PYTHONPATH", ""))
                os.execvpe(sys.executable, [sys.executable, "-c", child], env)
            finally:
                os._exit(127)
        set_size(self.master, 24, 80)
        self.buf = bytearray()
        self.status = None

    def pump(self, timeout=0.1):
        r, _, _ = select.select([self.master], [], [], timeout)
        if r:
            try:
                data = os.read(self.master, 65536)
            except OSError:
                data = b""
            if data:
                self.buf.extend(data)
                if b"\x1b[6n" in data:
                    try:
                        os.write(self.master, b"\x1b[24;1R")
                    except OSError:
                        pass
        if self.status is None:
            wpid, st = os.waitpid(self.pid, os.WNOHANG)
            if wpid == self.pid:
                self.status = st
                for _ in range(20):
                    self.pump(0.05)

    def wait_for(self, marker: bytes, timeout=30.0) -> bool:
        end = time.time() + timeout
        while time.time() < end:
            if marker in self.buf:
                return True
            self.pump(0.15)
        return marker in self.buf

    def send(self, seq, wait=0.4):
        try:
            os.write(self.master, seq)
        except OSError:
            pass
        time.sleep(wait)
        self.pump(0.25)

    def finish(self, timeout=25):
        end = time.time() + timeout
        while time.time() < end and self.status is None:
            self.pump(0.2)
        if self.status is None:
            try:
                os.kill(self.pid, signal.SIGKILL)
            except Exception:
                pass
            try:
                _, self.status = os.waitpid(self.pid, 0)
            except Exception:
                self.status = -1
        try:
            os.close(self.master)
        except OSError:
            pass
        return self.status


ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[a-zA-Z]|\x1b\][^\x07]*\x07")


def main() -> int:
    import pyte

    checks = []

    def check(name, cond):
        checks.append((name, bool(cond)))

    shutil.rmtree(FAKE_HOME, ignore_errors=True)
    os.makedirs(FAKE_HOME, exist_ok=True)
    try:
        os.remove("/tmp/cbhcli_confirm_phase.txt")
    except OSError:
        pass

    # ---------------- A. 全屏 ----------------
    r = Runner(CHILD_FS)
    ok_modal = r.wait_for("选择 [y/n/all/always]:".encode(), 40)
    check("全屏：选项块+模态提示出现", ok_modal)
    time.sleep(0.6)
    r.pump(0.3)
    snap = bytes(r.buf)

    screen = pyte.Screen(80, 24)
    stream = pyte.Stream(screen)
    stream.feed(snap.decode("utf-8", "replace"))
    rows = [ln.rstrip() for ln in screen.display]
    midx = next((i for i, ln in enumerate(rows)
                 if "选择 [y/n/all/always]:" in ln), None)
    check("全屏：模态提示行可见", midx is not None)
    # 选项块紧贴主区底部：always 行与模态行之间允许 ≥1 个空行
    # （我们 print 的空行 + Screen 待写空行），再往上依次 all/n/y/标题
    aidx = next((i for i in range(midx - 1, -1, -1)
                 if rows[i].lstrip().startswith("always")), None) \
        if midx is not None else None
    if aidx is not None and aidx >= 4:
        # 空行判定只看主区列（0..77）：第 78 列是滚动条（█/│），不算内容
        check("全屏：提示行上方不空行（always 紧贴模态行）",
              aidx == midx - 1)
        check("全屏：提示行下方空两行",
              midx + 2 < len(rows)
              and rows[midx + 1][:78].strip() == ""
              and rows[midx + 2][:78].strip() == "")
        check("全屏：always 往上依次 all / n / y",
              rows[aidx - 1].lstrip().startswith("all")
              and rows[aidx - 2].lstrip().startswith("n")
              and rows[aidx - 3].lstrip().startswith("y"))
        check("全屏：标题行「确认执行 terminal? 请选择」",
              "确认执行 terminal?" in rows[aidx - 4])
        check("全屏：y 行带说明", "本次放行" in rows[aidx - 3])
        check("全屏：n 行带说明", "本次拒绝" in rows[aidx - 2])
        check("全屏：all 行带说明", "不再确认" in rows[aidx - 1])
        check("全屏：always 行带说明", "永久" in rows[aidx])
    else:
        for nm in ("上方不空行", "下方空两行", "all/n/y", "标题", "y说明",
                   "n说明", "all说明", "always说明"):
            check(f"全屏：{nm}（选项行定位失败）", False)
    from cbhcli_pkg.core.ui.shell import _SPIN_FRAMES
    frames_fs = {ch for ch in _SPIN_FRAMES if ch.encode() in snap}
    check("全屏：待选择动画（≥2 个 braille 帧）", len(frames_fs) >= 2)
    # 黄色 #ffd75f：256 色 idx=221 或 truecolor 255;215;95（pt 按终端深度二选一）
    check("全屏：提示行黄色",
          b"38;5;221" in snap or b"38;2;255;215;95" in snap)

    r.send(b"y\r", 0.6)
    check("全屏：y 回车 → 确认 True",
          r.wait_for(b"CONFIRM-RESULT-1=True", 20))
    # 第二轮阶段标记走文件（不依赖渲染字节）
    phase_ok = False
    end = time.time() + 20
    while time.time() < end:
        try:
            with open("/tmp/cbhcli_confirm_phase.txt") as f:
                if f.read().strip() == "2":
                    phase_ok = True
                    break
        except OSError:
            pass
        r.pump(0.15)
    check("全屏：第二轮确认启动", phase_ok)
    time.sleep(0.8)
    r.pump(0.3)
    r.send(b"n\r", 0.6)
    check("全屏：n 回车 → 确认 False",
          r.wait_for(b"CONFIRM-RESULT-2=False", 20))
    st = r.finish()
    fs_text = bytes(r.buf).decode("utf-8", "replace")
    check("全屏：无 Traceback", "Traceback" not in fs_text)
    check("全屏：退出码 0",
          st is not None and os.WIFEXITED(st) and os.WEXITSTATUS(st) == 0)

    # ---------------- B. legacy ----------------
    r2 = Runner(CHILD_LEGACY)
    ok2 = r2.wait_for("选择 [y/n/all/always]:".encode(), 30)
    check("legacy：提示出现", ok2)
    time.sleep(0.4)
    r2.pump(0.3)
    r2.send(b"y\r", 0.6)
    check("legacy：y 回车 → 确认 True",
          r2.wait_for(b"CONFIRM-RESULT-1=True", 20))
    st2 = r2.finish()
    raw2 = bytes(r2.buf)
    plain = ANSI_RE.sub("", raw2.decode("utf-8", "replace"))
    # legacy 用 pyte 重放（"\n\033[1A" 下方空行技巧含光标回移，
    # 按字节行序断言会误判）
    scr2 = pyte.Screen(80, 24)
    pyte.Stream(scr2).feed(raw2.decode("utf-8", "replace"))
    rows2 = [ln.rstrip() for ln in scr2.display]
    pidx = next((i for i, ln in enumerate(rows2)
                 if "选择 [y/n/all/always]:" in ln), None)
    check("legacy：提示行可见", pidx is not None)
    if pidx is not None and pidx >= 1:
        check("legacy：提示行上方不空行（always 紧贴）",
              rows2[pidx - 1].lstrip().startswith("always"))
        check("legacy：提示行下方空两行",
              pidx + 2 < len(rows2)
              and rows2[pidx + 1].strip() == ""
              and rows2[pidx + 2].strip() == "")
        check("legacy：y/n/all 各独立成行",
              any(ln.lstrip().startswith("y") and "本次放行" in ln
                  for ln in rows2)
              and any(ln.lstrip().startswith("n") and "本次拒绝" in ln
                      for ln in rows2)
              and any(ln.lstrip().startswith("all") and "不再确认" in ln
                      for ln in rows2))
    frames_lg = {ch for ch in _SPIN_FRAMES if ch.encode() in raw2}
    check("legacy：待选择动画（≥2 个 braille 帧）", len(frames_lg) >= 2)
    check("legacy：提示行黄色",
          b"38;5;221" in raw2 or b"38;2;255;215;95" in raw2)
    check("legacy：无 Traceback", "Traceback" not in plain)
    check("legacy：退出码 0",
          st2 is not None and os.WIFEXITED(st2) and os.WEXITSTATUS(st2) == 0)

    shutil.rmtree(FAKE_HOME, ignore_errors=True)

    print("=" * 70)
    print("门禁：工具确认界面按行输出 + 与底部留空行（v5.5.4 迭代）")
    print("=" * 70)
    ok = True
    for name, cond in checks:
        print(f"  {'✅' if cond else '❌'} {name}")
        ok = ok and cond
    if not ok:
        open("/tmp/confirm_pty_fs.bin", "wb").write(bytes(r.buf))
        print("全屏输出已存 /tmp/confirm_pty_fs.bin")
        if midx is not None:
            print("pyte 屏幕快照：")
            for i, ln in enumerate(rows):
                print(f"  {i:02d}|{ln}")
        print("\n❌ FAIL")
        return 1
    print("\n✅ PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
