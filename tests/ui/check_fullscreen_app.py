#!/usr/bin/env python3
"""P4 门禁：真实 CBHCLIApp 全屏模式（启动 / 命令 / resize / Ctrl+C / SUSPEND / 退出）

用法：
    python tests/ui/check_fullscreen_app.py

流程：
    1. 启动 `CBHCLI_UI=fullscreen python -m cbhcli_pkg`，等欢迎面板
    2. `/help` → 等 "可用命令"（命令在工作线程执行，输出进主区）
    3. 连续 3 次 resize
    4. 空闲 Ctrl+C → 等 "操作被中断"
    5. `/tools off` → 触发 ask_text（SUSPEND 让出全屏）→ Esc 取消
    6. `quit` → 退出码 0、出现 "再见!"、无 Traceback

断言：
    - 状态栏含 "上下文:" 与进度条字符（D6-14）
    - 以上各阶段标记均出现
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


class Runner:
    def __init__(self):
        self.pid, self.master = pty.fork()
        if self.pid == 0:
            try:
                os.chdir(ROOT)
                env = os.environ.copy()
                env.update(
                    CBHCLI_UI="fullscreen",
                    TERM="xterm-256color",
                    COLUMNS="80",
                    LINES="24",
                    PYTHONPATH=ROOT + os.pathsep + env.get("PYTHONPATH", ""),
                )
                os.execvpe(sys.executable, [sys.executable, "-m", "cbhcli_pkg"], env)
            except Exception:
                os._exit(127)
        set_size(self.master, 24, 80)
        self.buf = bytearray()
        self.status = None

    def pump(self, timeout: float = 0.2) -> None:
        r, _, _ = select.select([self.master], [], [], timeout)
        if not r:
            return
        try:
            data = os.read(self.master, 65536)
        except OSError:
            data = b""
        if not data:
            return
        self.buf.extend(data)
        if b"\x1b[6n" in data:
            try:
                os.write(self.master, b"\x1b[24;1R")
            except OSError:
                pass

    def wait_for(self, marker: bytes, timeout: float = 40.0) -> bool:
        end = time.time() + timeout
        while time.time() < end:
            if marker in self.buf:
                return True
            self.pump()
            self._reap()
        return marker in self.buf

    def send(self, data: bytes) -> None:
        try:
            os.write(self.master, data)
        except OSError:
            pass

    def _reap(self) -> None:
        if self.status is not None:
            return
        wpid, wstatus = os.waitpid(self.pid, os.WNOHANG)
        if wpid == self.pid:
            self.status = wstatus
            # 收尾读取
            for _ in range(20):
                self.pump(0.1)

    def finish(self) -> int:
        end = time.time() + 15
        while time.time() < end and self.status is None:
            self.pump()
            self._reap()
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


def main() -> int:
    r = Runner()
    checks = []

    def check(name: str, cond: bool) -> None:
        checks.append((name, cond))

    check("欢迎面板", r.wait_for("AI 驱动的终端助手".encode(), 60))
    check("状态栏:上下文", "上下文:".encode() in r.buf)
    check("状态栏:进度条", b"\xe2\x96\x88" in r.buf and b"\xe2\x96\x91" in r.buf)

    # 用 /ctx：输出较短，结尾标记一定在可见区域（长输出的开头会滚出屏幕）
    r.send(b"/ctx\r")
    check("/ctx 输出", r.wait_for("压缩阈值".encode(), 40))

    for rows, cols in ((12, 40), (30, 100), (8, 30), (24, 80)):
        time.sleep(0.3)
        set_size(r.master, rows, cols)
    time.sleep(0.6)

    r.send(b"\x03")  # 空闲 Ctrl+C
    check("空闲 Ctrl+C", r.wait_for("操作被中断".encode(), 20))

    time.sleep(0.3)
    r.send(b"/tools off\r")
    time.sleep(2.0)          # 等命令进入 SUSPEND 后的交互提问
    r.send(b"0\r")           # /tools 的取消编号是 0（q 属于 skills 的语义）
    check("/tools off 取消", r.wait_for("已取消".encode(), 30))

    r.send(b"quit\r")
    status = r.finish()

    text = bytes(r.buf)
    check("退出码 0", status is not None and os.WIFEXITED(status) and os.WEXITSTATUS(status) == 0)
    check("再见", "再见".encode() in text)
    check("无 Traceback", b"Traceback" not in text)
    check("退出 dump(欢迎面板重复出现)", text.count("AI 驱动的终端助手".encode()) >= 2)

    print("=" * 70)
    print("P4 门禁：真实 CBHCLIApp 全屏模式")
    print("=" * 70)
    print(f"输出 {len(text)}B  状态 {status}")
    ok = True
    for name, cond in checks:
        print(f"  {'✅' if cond else '❌'} {name}")
        ok = ok and cond
    if not ok:
        if b"Traceback" in text:
            i = text.find(b"Traceback")
            print(text[max(0, i - 200):i + 1800].decode("utf-8", "replace"))
        print("\n❌ FAIL")
        return 1
    print("\n✅ PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
