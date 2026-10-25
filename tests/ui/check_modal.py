#!/usr/bin/env python3
"""P5b 门禁：应用内模态（不再 SUSPEND 旧 PromptSession）

用法：
    python tests/ui/check_modal.py

流程（真实 CBHCLIApp，CBHCLI_UI=fullscreen）：
    1. `/tools off` → 菜单 + 提示行都应画在全屏主区（可见）
    2. 断言期间**没有** `\\x1b[?1049l`（未离开 alt screen）
    3. Ctrl+C 取消模态 → `已取消`
    4. 再来一次，输入 `0` 回车 → `已取消`（提交路径）
    5. quit → 退出码 0

契约：K-17 优先级 1（模态 Ctrl+C 只取消模态）、D7-04、X-01
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
            return
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

    check("启动", r.wait_for("AI 驱动的终端助手".encode(), 60))

    # 第一次：模态 + Ctrl+C 取消
    r.send(b"/tools off\r")
    check("菜单/提示可见", r.wait_for("请选择编号".encode(), 30))
    check("未离开 alt screen（无 SUSPEND）",
          b"\x1b[?1049l" not in bytes(r.buf))
    time.sleep(0.4)
    r.send(b"\x03")
    check("Ctrl+C 取消模态", r.wait_for("已取消".encode(), 15))

    # 第二次：模态 + 输入 0 回车
    time.sleep(0.5)
    r.send(b"/tools off\r")
    time.sleep(1.2)
    r.send(b"0\r")
    check("模态提交（0=取消）", r.wait_for("已取消".encode(), 20))

    time.sleep(0.5)
    r.send(b"quit\r")
    status = r.finish()
    text = bytes(r.buf)
    check("退出码 0", status is not None and os.WIFEXITED(status) and os.WEXITSTATUS(status) == 0)
    check("退出 dump + 再见", "再见".encode() in text)
    check("无 Traceback", b"Traceback" not in text)

    print("=" * 70)
    print("P5b 门禁：应用内模态")
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
