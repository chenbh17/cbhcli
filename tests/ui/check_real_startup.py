#!/usr/bin/env python3
"""P1 门禁（真实流程）：启动欢迎面板在 legacy / sink 下逐字节一致

用法：
    python tests/ui/check_real_startup.py

做法：
    - 子进程为真实 CLI（`python -m cbhcli_pkg`），stdout 接 pty（80x24）；
    - 自动应答 CPR（`\\x1b[6n` → `\\x1b[24;1R`），否则 prompt_toolkit 渲染会卡住；
    - 截取"启动到首个输入提示符 `❯`"这段**确定性的欢迎面板**做逐字节比较；
    - 之后发送 `quit`，确认进程能正常退出（退出码一致）。

说明：`❯` 之后的输入框重绘受时序影响（帧数不固定），不属于 P1 门禁比较范围。
"""

from __future__ import annotations

import fcntl
import os
import pty
import select
import signal
import struct
import subprocess
import sys
import termios
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _set_winsize(fd: int, rows: int = 24, cols: int = 80) -> None:
    try:
        fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
    except Exception:
        pass


def run_mode(mode: str, timeout: float = 40.0):
    master, slave = pty.openpty()
    _set_winsize(slave)
    env = os.environ.copy()
    env["CBHCLI_UI"] = mode
    env["TERM"] = "xterm-256color"
    env["COLUMNS"] = "80"
    env["LINES"] = "24"
    env["PYTHONPATH"] = ROOT + os.pathsep + env.get("PYTHONPATH", "")
    p = subprocess.Popen(
        [sys.executable, "-m", "cbhcli_pkg"],
        stdin=slave,
        stdout=slave,
        stderr=subprocess.DEVNULL,
        env=env,
        cwd=ROOT,
        start_new_session=True,
    )
    os.close(slave)

    buf = bytearray()
    prefix = b""
    deadline = time.time() + timeout
    sent_quit = False
    while time.time() < deadline:
        r, _, _ = select.select([master], [], [], 0.2)
        if r:
            try:
                data = os.read(master, 65536)
            except OSError:
                break
            if not data:
                break
            buf.extend(data)
            # 应答光标位置查询，否则渲染器会等待 CPR
            if b"\x1b[6n" in data:
                try:
                    os.write(master, b"\x1b[24;1R")
                except OSError:
                    pass
        if not prefix and "❯".encode() in buf:
            cut = buf.find("❯".encode())
            prefix = bytes(buf[:cut])
            sent_quit = True
            try:
                # 注意：Enter 必须发 \r（c-m）；\n 是 Ctrl+J（我们的绑定是"插入换行"）
                os.write(master, b"quit\r")
            except OSError:
                pass
        if sent_quit and p.poll() is not None:
            # 收尾
            while True:
                r, _, _ = select.select([master], [], [], 0.3)
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
    try:
        rc = p.wait(timeout=8)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(p.pid, signal.SIGKILL)
        except Exception:
            pass
        rc = -9
    os.close(master)
    return prefix, bytes(buf), rc


def main() -> int:
    print("=" * 70)
    print("P1 门禁（真实流程）：启动欢迎面板字节一致性")
    print("=" * 70)
    pa, full_a, rc_a = run_mode("legacy")
    pb, full_b, rc_b = run_mode("sink")
    print(f"欢迎面板字节: legacy={len(pa)}B  sink={len(pb)}B  "
          f"{'一致' if pa == pb else '不一致'}")
    print(f"完整输出字节: legacy={len(full_a)}B  sink={len(full_b)}B")
    print(f"退出码: legacy={rc_a}  sink={rc_b}")
    ok = (pa == pb) and (rc_a == rc_b == 0)
    if not ok:
        if pa != pb:
            n = min(len(pa), len(pb))
            for i in range(n):
                if pa[i] != pb[i]:
                    lo = max(0, i - 60)
                    print(f"首个差异 @ {i}\n legacy={pa[lo:i+80]!r}\n sink  ={pb[lo:i+80]!r}")
                    break
            else:
                lo = max(0, n - 60)
                print(f"长度不同：legacy={len(pa)} sink={len(pb)}\n"
                      f" 尾部 legacy={pa[lo:]!r}\n 尾部 sink  ={pb[lo:]!r}")
        print("\n❌ FAIL")
        return 1
    print("\n✅ PASS（欢迎面板逐字节一致；两模式均正常退出）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
