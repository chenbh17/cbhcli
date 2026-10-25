#!/usr/bin/env python3
"""P3 冒烟（中断）：流式中 Ctrl+C 取消本轮，输入区仍可用

用法：
    python tests/ui/check_shell_interrupt.py

断言：
    - 流式进行中按 Ctrl+C → 本轮在 ~1s 内停止；
    - 输出出现 "操作被中断。"，且**不出现** `[stream-done]`（说明确实没跑完）；
    - 中断后还能提交 `quit` → 退出码 0；
    - 无 Traceback。
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
DEMO = os.path.join(ROOT, "tests", "ui", "demo_hybrid.py")


def set_size(fd: int, rows: int, cols: int) -> None:
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))


def main() -> int:
    pid, master = pty.fork()
    if pid == 0:
        try:
            os.chdir(ROOT)
            env = os.environ.copy()
            env["TERM"] = "xterm-256color"
            env["COLUMNS"] = "80"
            env["LINES"] = "24"
            env["PYTHONPATH"] = ROOT + os.pathsep + env.get("PYTHONPATH", "")
            os.execvpe(sys.executable, [sys.executable, DEMO], env)
        except Exception:
            os._exit(127)

    set_size(master, 24, 80)
    buf = bytearray()
    sent_hello = sent_ctrl_c = sent_quit = False
    t_start = time.time()
    deadline = t_start + 30
    status = None

    while time.time() < deadline:
        r, _, _ = select.select([master], [], [], 0.15)
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

        if not sent_hello and b"P3" in buf:
            time.sleep(0.3)
            os.write(master, b"hello\r")
            sent_hello = True

        # 等流式跑起来（出现若干行）再中断
        if sent_hello and not sent_ctrl_c and b"[mid-stream]" in buf:
            time.sleep(0.2)
            os.write(master, b"\x03")  # Ctrl+C（raw 模式下是按键 c-c）
            sent_ctrl_c = True

        if sent_ctrl_c and not sent_quit and "操作被中断".encode() in buf:
            time.sleep(0.2)
            os.write(master, b"quit\r")
            sent_quit = True

        wpid, wstatus = os.waitpid(pid, os.WNOHANG)
        if wpid == pid:
            status = wstatus
            while True:
                r, _, _ = select.select([master], [], [], 0.2)
                if not r:
                    break
                try:
                    data = os.read(master, 65536)
                except OSError:
                    break
                if not data:
                    break
                buf.extend(data)
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
    print("=" * 70)
    print("P3 冒烟（中断）")
    print("=" * 70)
    print(f"退出状态: {status}  Ctrl+C: {sent_ctrl_c}  quit: {sent_quit}  "
          f"输出 {len(text)}B")
    print(f"  含 '操作被中断': {'操作被中断'.encode() in text}")
    print(f"  含 '[stream-done]': {b'[stream-done]' in text}（应为 False）")

    ok = (
        status is not None
        and os.WIFEXITED(status)
        and os.WEXITSTATUS(status) == 0
        and sent_ctrl_c
        and sent_quit
        and "操作被中断".encode() in text
        and b"[stream-done]" not in text
        and b"Traceback" not in text
    )
    if ok:
        print("\n✅ PASS")
        return 0
    print("\n❌ FAIL")
    if b"Traceback" in text:
        idx = text.find(b"Traceback")
        print(text[max(0, idx - 200):idx + 1200].decode("utf-8", "replace"))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
