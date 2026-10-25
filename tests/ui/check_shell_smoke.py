#!/usr/bin/env python3
"""P3 冒烟：全屏 Shell 启停 / 流式 / resize / 滚动 / 退出

用法：
    python tests/ui/check_shell_smoke.py

做法：
    - `pty.fork()` 启动 `tests/ui/demo_hybrid.py`（子进程拥有真正的控制终端，
      `TIOCSWINSZ` 会触发 SIGWINCH，prompt_toolkit 能收到 resize）；
    - 自动应答 CPR（`\\x1b[6n`）；
    - 依次：等待首屏 → 输入 hello → 等待流式结束标记 → 3 次 resize → PgUp → quit；
    - 断言：退出码 0、无 Traceback、关键文本出现。
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
    if pid == 0:  # child
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
    deadline = time.time() + 40
    sent_hello = sent_quit = False
    resized = 0
    pgup_sent = False
    status = None

    while time.time() < deadline:
        r, _, _ = select.select([master], [], [], 0.2)
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
            time.sleep(0.4)
            os.write(master, b"hello\r")
            sent_hello = True

        if sent_hello and not pgup_sent and b"[stream-done]" in buf:
            # 流式结束 → 连续 resize（每次等 0.4s 让渲染稳定）
            for (rows, cols) in ((12, 40), (30, 100), (8, 30)):
                time.sleep(0.3)
                set_size(master, rows, cols)
                resized += 1
            time.sleep(0.4)
            os.write(master, b"\x1b[5~")  # PgUp
            pgup_sent = True
            time.sleep(0.3)
            os.write(master, b"quit\r")
            sent_quit = True

        wpid, wstatus = os.waitpid(pid, os.WNOHANG)
        if wpid == pid:
            status = wstatus
            # 收尾
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
    print("P3 冒烟：全屏 Shell")
    print("=" * 70)
    print(f"退出状态: {status}  resize 次数: {resized}  "
          f"已提交 hello: {sent_hello}  已退出: {sent_quit}")
    print(f"输出大小: {len(text)}B")
    for marker in ("P3 全屏 Demo", "第 0 行", "[stream-done]", "● 完成"):
        print(f"  含 {marker!r}: {marker.encode() in text}")

    ok = (
        status is not None
        and os.WIFEXITED(status)
        and os.WEXITSTATUS(status) == 0
        and sent_hello
        and resized == 3
        and b"Traceback" not in text
        and b"[stream-done]" in text
        and b"\xe2\x97\x8f \xe5\xae\x8c\xe6\x88\x90" in text  # "● 完成"
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
