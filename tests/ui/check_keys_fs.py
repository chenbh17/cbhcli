#!/usr/bin/env python3
"""P6 门禁：全屏模式下的键位与滚动（K-18 / T-13）

用法：
    python tests/ui/check_keys_fs.py

覆盖：
    - Ctrl+R（详细/简洁切换，打印提示）
    - Shift+Tab（权限模式循环 / YOLO 警告）
    - PgUp / PgDn / Ctrl+Home / Ctrl+End（滚动，不崩溃）
    - Ctrl+L（清空主区）
    - Ctrl+D（空输入退出）
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


def set_size(fd: int, rows: int, cols: int) -> None:
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))


def main() -> int:
    pid, master = pty.fork()
    if pid == 0:
        os.chdir(ROOT)
        env = os.environ.copy()
        env.update(CBHCLI_UI="fullscreen", TERM="xterm-256color",
                   COLUMNS="80", LINES="24",
                   PYTHONPATH=ROOT + os.pathsep + env.get("PYTHONPATH", ""))
        try:
            os.execvpe(sys.executable, [sys.executable, "-m", "cbhcli_pkg"], env)
        except Exception:
            os._exit(127)
    set_size(master, 24, 80)

    buf = bytearray()
    t0 = time.time()
    status = None
    stage = 0
    last = time.time()

    def send(data: bytes):
        try:
            os.write(master, data)
        except OSError:
            pass

    while time.time() - t0 < 60:
        r, _, _ = select.select([master], [], [], 0.1)
        if r:
            try:
                d = os.read(master, 65536)
            except OSError:
                d = b""
            if d:
                buf.extend(d)
                if b"\x1b[6n" in d:
                    send(b"\x1b[24;1R")

        if stage == 0 and "AI 驱动的终端助手".encode() in buf:
            time.sleep(0.5)
            send(b"/help\r")
            stage = 1
            last = time.time()
        elif stage == 1 and time.time() - last > 3:
            send(b"\x1b[5~")     # PgUp
            send(b"\x1b[6~")     # PgDn
            send(b"\x1b[1;5H")   # Ctrl+Home
            send(b"\x1b[1;5F")   # Ctrl+End
            stage = 2
            last = time.time()
        elif stage == 2 and time.time() - last > 1:
            send(b"\x0c")        # Ctrl+L
            time.sleep(0.5)
            send(b"\x12")        # Ctrl+R
            stage = 3
            last = time.time()
        elif stage == 3 and time.time() - last > 1:
            send(b"\x1b[Z")      # Shift+Tab
            stage = 4
            last = time.time()
        elif stage == 4 and time.time() - last > 1:
            send(b"\x04")        # Ctrl+D（空输入退出）
            stage = 5
            last = time.time()

        w, s = os.waitpid(pid, os.WNOHANG)
        if w == pid:
            status = s
            for _ in range(20):
                rr, _, _ = select.select([master], [], [], 0.1)
                if not rr:
                    break
                try:
                    d = os.read(master, 65536)
                except OSError:
                    break
                if not d:
                    break
                buf.extend(d)
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
    checks = [
        ("退出码 0（Ctrl+D）", status is not None and os.WIFEXITED(status) and os.WEXITSTATUS(status) == 0),
        ("Ctrl+R 提示", "工具显示: 详细".encode() in text),
        ("Shift+Tab 提示", ("权限模式:".encode() in text) or ("YOLO 模式".encode() in text)),
        ("再见", "再见".encode() in text),
        ("无 Traceback", b"Traceback" not in text),
    ]

    print("=" * 70)
    print("P6 门禁：键位与滚动")
    print("=" * 70)
    print(f"输出 {len(text)}B  状态 {status}")
    ok = True
    for name, cond in checks:
        print(f"  {'✅' if cond else '❌'} {name}")
        ok = ok and cond
    if not ok and b"Traceback" in text:
        i = text.find(b"Traceback")
        print(text[max(0, i - 200):i + 1500].decode("utf-8", "replace"))
    print("\n✅ PASS" if ok else "\n❌ FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
