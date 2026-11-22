#!/usr/bin/env python3
"""v5.5.3 门禁：聊天输入框 Ctrl+Z 撤销 / Ctrl+Y 重做（全屏 + legacy 双模式）

用法：
    /usr/bin/python3 tests/ui/check_undo_redo.py

做法：
    - 全屏：最小 FullScreenUI；legacy：ChatInputBox + 极简 fake app（均不接 AI）；
    - 提交文本写入 SUBMIT_LOG 文件（精确断言，不受全屏差分重绘影响——
      pt 是单元格级重绘，原始字节里找整行文本不可靠，见 v5.5.0 教训）；
    - pty 一次性发送按键序列，断言提交序列恰好为：
        A  基础撤销：abcd + Ctrl+Z → abc
        B  连续撤销×2 + 重做×2：abc → a → abc（验证 save_before=False，
           否则 redo 栈被键位处理器提前清空，重做失效）
        C  Ctrl+U 整行剪切后 Ctrl+Z 恢复 hello
        D  撤销后输入新字符 → redo 栈清空（Ctrl+Y 不再恢复旧文本）→ ac
        E  空缓冲上 Ctrl+Z/Ctrl+Y 安全无副作用 → ok
    - 最后 Ctrl+D 退出。
"""

from __future__ import annotations

import fcntl
import os
import pty
import select
import signal
import struct
import sys
import tempfile
import termios
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

CHILD_FS = r"""
import asyncio, os, sys
sys.path.insert(0, os.environ["ROOT"])
from cbhcli_pkg.core.ui.shell import FullScreenUI

ui = FullScreenUI(status_provider=lambda: [("class:bottom-toolbar", "UNDO")])
log = open(os.environ["SUBMIT_LOG"], "a", encoding="utf-8")

def on_submit(text):
    log.write("SUBMIT:%s\n" % text); log.flush()
    ui.feed("\nSUBMIT:%s\n" % text)

ui.on_submit = on_submit
ui.feed("READY-UNDO\n")
asyncio.run(ui.run_async())
"""

CHILD_LEGACY = r"""
import os, sys, types
sys.path.insert(0, os.environ["ROOT"])
os.environ["CBHCLI_UI"] = "legacy"
from cbhcli_pkg.core.input_box import ChatInputBox

app = types.SimpleNamespace(
    llm_client=None, current_agent_config=None, session=None,
    context_window=None, current_agent_name="test", skill_manager=None,
)
box = ChatInputBox(cmd_helper=object(), app=app)
log = open(os.environ["SUBMIT_LOG"], "a", encoding="utf-8")
print("READY-UNDO", flush=True)
while True:
    text = box.prompt()
    if text == "quit":
        break
    log.write("SUBMIT:%s\n" % text); log.flush()
    print("SUBMIT:%s" % text, flush=True)
"""

# (按键, 发送后等待秒数)
STEPS = [
    # A 基础撤销：abcd → abc
    (b"abcd", 0.20), (b"\x1a", 0.12), (b"\r", 0.50),
    # B 撤销×2 + 重做×2：abc → ab → a → ab → abc
    (b"abc", 0.20), (b"\x1a", 0.12), (b"\x1a", 0.12),
    (b"\x19", 0.12), (b"\x19", 0.12), (b"\r", 0.50),
    # C Ctrl+U 剪切后撤销恢复
    (b"hello", 0.20), (b"\x15", 0.12), (b"\x1a", 0.12), (b"\r", 0.50),
    # D 撤销后新输入清空 redo 栈：ab → a → ac（Ctrl+Y 无效果）
    (b"ab", 0.20), (b"\x1a", 0.12), (b"c", 0.12), (b"\x19", 0.12), (b"\r", 0.50),
    # E 空缓冲撤销/重做安全
    (b"\x1a", 0.12), (b"\x19", 0.12), (b"ok", 0.20), (b"\r", 0.50),
    # 退出
    (b"\x04", 0.30),
]

EXPECTED = ["SUBMIT:abc", "SUBMIT:abc", "SUBMIT:hello", "SUBMIT:ac", "SUBMIT:ok"]


def run_pty(child_src: str, label: str) -> bool:
    log_path = os.path.join(tempfile.mkdtemp(prefix="cbhcli_undo_"), "submits.log")
    pid, master = pty.fork()
    if pid == 0:
        os.chdir(ROOT)
        env = os.environ.copy()
        env.update(TERM="xterm-256color", COLUMNS="80", LINES="24",
                   PYTHONPATH=ROOT, ROOT=ROOT, SUBMIT_LOG=log_path)
        try:
            os.execvpe(sys.executable, [sys.executable, "-c", child_src], env)
        except Exception:
            os._exit(127)
    fcntl.ioctl(master, termios.TIOCSWINSZ, struct.pack("HHHH", 24, 80, 0, 0))

    buf = bytearray()
    t0 = time.time()
    sent = False
    status = None
    step_idx = 0
    next_at = 0.0

    while time.time() - t0 < 45:
        r, _, _ = select.select([master], [], [], 0.08)
        if r:
            try:
                d = os.read(master, 65536)
            except OSError:
                d = b""
            if d:
                buf.extend(d)
                if b"\x1b[6n" in d:
                    try:
                        os.write(master, b"\x1b[24;1R")
                    except OSError:
                        pass
        if sent and step_idx < len(STEPS) and time.time() >= next_at:
            data, delay = STEPS[step_idx]
            try:
                os.write(master, data)
            except OSError:
                pass
            step_idx += 1
            next_at = time.time() + delay
        if not sent and b"READY-UNDO" in buf:
            time.sleep(0.3)
            sent = True
            next_at = time.time()
        w, s = os.waitpid(pid, os.WNOHANG)
        if w == pid:
            status = s
            break

    if status is None:
        try:
            os.kill(pid, signal.SIGKILL)
        except Exception:
            pass
        try:
            _, status = os.waitpid(pid, 0)
        except Exception:
            status = -1
    try:
        os.close(master)
    except OSError:
        pass

    text = bytes(buf)
    try:
        with open(log_path, encoding="utf-8") as f:
            lines = f.read().splitlines()
    except OSError:
        lines = []
    checks = [
        ("A 基础撤销 abcd→abc", lines.count("SUBMIT:abc") >= 1),
        ("B 撤销×2+重做×2 回到 abc", lines.count("SUBMIT:abc") >= 2),
        ("C Ctrl+U 后撤销恢复 hello", "SUBMIT:hello" in lines),
        ("D 新输入清空 redo 栈 (ac)", "SUBMIT:ac" in lines),
        ("E 空缓冲撤销/重做安全", "SUBMIT:ok" in lines),
        ("提交序列完全一致", lines == EXPECTED),
        ("Ctrl+D 退出", status is not None and os.WIFEXITED(status)
         and os.WEXITSTATUS(status) == 0),
        ("无 Traceback", b"Traceback" not in text),
    ]

    print("=" * 70)
    print(f"v5.5.3 门禁：Ctrl+Z/Ctrl+Y 撤销重做 — {label}")
    print("=" * 70)
    print(f"输出 {len(text)}B  状态 {status}  提交 {lines}")
    ok = True
    for name, cond in checks:
        print(f"  {'✅' if cond else '❌'} {name}")
        ok = ok and cond
    if not ok:
        print("--- 期望序列 ---")
        print(EXPECTED)
    return ok


def main() -> int:
    ok_fs = run_pty(CHILD_FS, "全屏 TUI")
    ok_legacy = run_pty(CHILD_LEGACY, "legacy 内联")
    ok = ok_fs and ok_legacy
    print("\n✅ PASS" if ok else "\n❌ FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
