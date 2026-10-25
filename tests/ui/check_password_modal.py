#!/usr/bin/env python3
"""P5b 门禁：password 模态不回显（契约 D7-05 / Q-02=A）

用法：
    python tests/ui/check_password_modal.py

做法：
    - 子进程起一个最小 FullScreenUI；
    - 后台线程调用 `ui.ask_text_sync("  > ", password=True)`，拿到结果后打印
      `RESULT:<文本>` 并退出；
    - 父进程先输入 `secret`（不回车），断言此时的输出里**没有** secret、也没有 `*`；
    - 再回车，断言出现 `RESULT:secret`（说明值确实被读到）。
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

CHILD = r"""
import asyncio, os, sys, threading
sys.path.insert(0, os.environ["ROOT"])
from cbhcli_pkg.core.ui.shell import FullScreenUI

def status():
    return [("class:bottom-toolbar", "PWD-MODAL")]

ui = FullScreenUI(status_provider=status)

def worker():
    import time
    time.sleep(0.6)
    val = ui.ask_text_sync("  > ", password=True)
    ui.feed("\nRESULT:%s\n" % (val or ""))
    import time as _t
    _t.sleep(0.3)
    loop = getattr(ui.app, "loop", None)
    if loop is not None and loop.is_running():
        loop.call_soon_threadsafe(ui.app.exit)
    else:
        ui.app.exit()

threading.Thread(target=worker, daemon=True).start()
asyncio.run(ui.run_async())
"""


def main() -> int:
    pid, master = pty.fork()
    if pid == 0:
        os.chdir(ROOT)
        env = os.environ.copy()
        env.update(TERM="xterm-256color", COLUMNS="80", LINES="24",
                   PYTHONPATH=ROOT, ROOT=ROOT)
        try:
            os.execvpe(sys.executable, [sys.executable, "-c", CHILD], env)
        except Exception:
            os._exit(127)
    fcntl.ioctl(master, termios.TIOCSWINSZ, struct.pack("HHHH", 24, 80, 0, 0))

    buf = bytearray()
    t0 = time.time()
    sent_text = sent_enter = False
    before_enter = b""
    status = None

    while time.time() - t0 < 20:
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
        if not sent_text and b"  > " in buf:
            time.sleep(0.3)
            os.write(master, b"secret")
            sent_text = True
            time.sleep(0.5)
            before_enter = bytes(buf)
            os.write(master, b"\r")
            sent_enter = True
        if sent_enter and b"RESULT:" in buf:
            time.sleep(0.4)
            break
        w, s = os.waitpid(pid, os.WNOHANG)
        if w == pid:
            status = s
            break

    if status is None:
        w, s = os.waitpid(pid, os.WNOHANG)
        if w == pid:
            status = s
        else:
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
    masked = (b"secret" not in before_enter) and (b"*" not in before_enter)
    printed = b"RESULT:secret" in text

    print("=" * 70)
    print("P5b 门禁：password 模态不回显")
    print("=" * 70)
    print(f"输出 {len(text)}B  状态 {status}")
    print(f"  输入过程未泄露: {masked}")
    print(f"  回车后拿到值:   {printed}")
    ok = masked and printed and status is not None and os.WIFEXITED(status) and os.WEXITSTATUS(status) == 0
    print("\n✅ PASS" if ok else "\n❌ FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
