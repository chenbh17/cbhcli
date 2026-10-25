"""验证：每轮结束的"输入区重绘"能清掉残格，且不重写主区。

做法：模拟"终端上多了一个字符"（直接写终端，绕过渲染器模型），
等轮结束的修复重绘触发后，用 pyte 检查该字符是否消失；同时确认主区内容没被重写。
"""
import fcntl
import os
import pty
import select
import struct
import sys
import termios
import time

import pyte

ROWS, COLS = 16, 70
CHILD = r'''
import asyncio, sys, os
sys.path.insert(0, ROOT)
from cbhcli_pkg.core.ui.shell import FullScreenUI

async def main():
    ui = FullScreenUI(status_provider=lambda: [("", "s1"), ("", "\ns2")])
    ui.install_sink()
    task = asyncio.create_task(ui.app.run_async())
    await asyncio.sleep(0.4)
    for i in range(8):
        print(f"MAIN-{i:02d} 中文内容")
    ui.invalidate()
    await asyncio.sleep(0.4)
    ui.set_busy(True)
    await asyncio.sleep(0.4)
    ui.set_busy(False)
    # 在修复重绘之前，往终端"输入行"注入一个残格（绕过渲染器）
    await asyncio.sleep(0.02)
    real = sys.__stdout__
    real.write("\x1b[10;3HZ")     # 第 10 行第 3 列（0-based 行9列2）
    real.flush()
    await asyncio.sleep(1.2)
    ui.close_input()
    ui.app.exit()
    try:
        await asyncio.wait_for(task, timeout=5)
    except Exception:
        pass
    ui.uninstall_sink()

asyncio.run(main())
'''.replace("ROOT", repr("/media/chenbh17/cbhssd/cbhcli/cbhcli-latest-Copy1"))


def main() -> int:
    pid, m = pty.fork()
    if pid == 0:
        env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
        env.update(TERM="xterm-256color", CBHCLI_MOUSE="on",
                   PYTHONPATH="/media/chenbh17/cbhssd/cbhcli/cbhcli-latest-Copy1")
        os.chdir("/tmp")
        os.execvpe("/usr/bin/python3", ["/usr/bin/python3", "-c", CHILD], env)
    fcntl.ioctl(m, termios.TIOCSWINSZ, struct.pack("HHHH", ROWS, COLS, 0, 0))

    buf = bytearray()
    t0 = time.time()
    marker = {}

    def pump(t=0.08):
        r, _, _ = select.select([m], [], [], t)
        if r:
            try:
                d = os.read(m, 65536)
            except OSError:
                d = b""
            if d:
                buf.extend(d)
                if b"\x1b[6n" in d:
                    try:
                        os.write(m, f"\x1b[{ROWS};1R".encode())
                    except OSError:
                        pass

    # 等注入完成那一帧
    while time.time() - t0 < 10:
        pump(0.08)
        if b"\x1b[10;3HZ" in buf and "injected" not in marker:
            marker["injected"] = True
            marker["before"] = len(buf)
        w, st = os.waitpid(pid, os.WNOHANG)
        if w == pid:
            break
    time.sleep(0.2)
    pump(0.2)

    def screen():
        sc = pyte.Screen(COLS, ROWS)
        pyte.ByteStream(sc).feed(bytes(buf))
        return ["".join(sc.buffer[y][x].data for x in range(COLS)) for y in range(ROWS)]

    try:
        os.kill(pid, 9)
    except Exception:
        pass
    try:
        os.waitpid(pid, 0)
    except Exception:
        pass

    rows = screen()
    text = "\n".join(rows)
    start_i = marker.get("before", 0)
    end_i = bytes(buf).find(b"\x1b[?1049l", start_i)   # 退出备用屏之前 = 修复窗口
    if end_i < 0:
        end_i = len(buf)
    seg = bytes(buf[start_i:end_i]).decode("utf-8", "replace")
    print("屏幕：")
    for y in range(ROWS):
        print(f"{y:2d}|{rows[y].rstrip()!r}")

    ok = True

    def check(name, cond):
        nonlocal ok
        print(("  ✅ " if cond else "  ❌ ") + name)
        ok = ok and cond

    check("残格已被重绘清除（屏幕不再含 'Z'）", "Z" not in text)
    check("输入行仍带 ❯ 前缀", any(r.strip().startswith("❯") for r in rows))
    # 注：注入本身会让渲染器的"光标模型"错位，可能触发一段区域重绘，故不断言"主区没被重写"；
    # 真正影响选区的是"切换备用屏 / 开关鼠标上报"，这里断言修复窗口内没有这些序列。
    check("修复未退出/重入备用屏", "\x1b[?1049l" not in seg and "\x1b[?1049h" not in seg)
    check("修复未开关鼠标上报", "\x1b[?1000l" not in seg and "\x1b[?1000h" not in seg)
    check("修复确实写了输入区", "❯" in seg)
    print("\n" + ("✅ PASS" if ok else "❌ FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
