#!/usr/bin/env python3
"""P6 门禁：退出 dump + 页脚 transcript 路径（D0-09 / S-11）

用法：
    python tests/ui/check_exit_dump.py

流程：
    - 真实 CBHCLIApp 全屏启动；
    - 连续两次 `/help`（累计 >200 行）；
    - quit；
    - 断言：退出码 0、dump 页脚含「完整记录:」+ 路径、transcript 文件存在、
      内容含帮助文本且**不含 ANSI 转义**。
"""

from __future__ import annotations

import fcntl
import glob
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
    stage = 0
    status = None
    last_pump = time.time()

    while time.time() - t0 < 90:
        r, _, _ = select.select([master], [], [], 0.12)
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

        if stage == 0 and "AI 驱动的终端助手".encode() in buf:
            time.sleep(0.5)
            os.write(master, b"/help\r")
            stage = 1
            last_pump = time.time()
        elif 1 <= stage <= 3 and time.time() - last_pump > 4:
            if stage < 3:
                os.write(master, b"/help\r")   # 共 3 次，确保 >200 行
                stage += 1
            else:
                os.write(master, b"quit\r")
                stage = 4
            last_pump = time.time()

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
    # 最新的 transcript
    files = sorted(
        glob.glob(os.path.expanduser("~/.cbhcli/agents/*/history/transcripts/*.log")),
        key=lambda p: os.path.getmtime(p),
    )
    latest = files[-1] if files else None
    content = ""
    if latest:
        with open(latest, encoding="utf-8", errors="replace") as f:
            content = f.read()

    checks = [
        ("退出码 0", status is not None and os.WIFEXITED(status) and os.WEXITSTATUS(status) == 0),
        ("dump 页脚", "以上为最近 200 行".encode() in text and "完整记录:".encode() in text),
        ("再见", "再见".encode() in text),
        ("transcript 存在", bool(latest)),
        ("transcript 含帮助文本", "可用命令" in content and "用法:" in content),
        ("transcript 无 ANSI", "\x1b" not in content),
        ("无 Traceback", b"Traceback" not in text),
    ]

    print("=" * 70)
    print("P6 门禁：退出 dump + transcript")
    print("=" * 70)
    print(f"输出 {len(text)}B  状态 {status}")
    print(f"transcript: {latest}  ({len(content)}B)")
    ok = True
    for name, cond in checks:
        print(f"  {'✅' if cond else '❌'} {name}")
        ok = ok and cond
    if not ok and latest:
        print("--- transcript 前 400 字符 ---")
        print(content[:400])
    print("\n✅ PASS" if ok else "\n❌ FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
