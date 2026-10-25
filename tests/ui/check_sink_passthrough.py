#!/usr/bin/env python3
"""P1 门禁：SINK 透传模式必须与改造前**逐字节一致**

用法：
    python tests/ui/check_sink_passthrough.py

原理：
    1. 把同一段"输出脚本"分别在 `CBHCLI_UI=legacy` 与 `CBHCLI_UI=sink`
       下跑一次（子进程，stdout 接到 pty，stderr 接到管道）；
    2. 对比两次捕获到的 stdout 字节、stderr 字节；
    3. 另做若干单元断言（语义 API 的原始序列、属性透传、close 不关真实流）。

契约：docs/全屏重构行为契约.md → H-05 / D2-08 / T-11 / N-08 / V-01~05
"""

from __future__ import annotations

import fcntl
import io
import os
import pty
import select
import struct
import subprocess
import sys
import termios

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# 优先导入源码树（否则会命中 ~/.local 里已安装的旧包，没有 core.ui）
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
CHILD = r"""
import os, sys
from cbhcli_pkg.core.ui.sink import install, uninstall, get_sink, is_installed, ui_mode

if ui_mode() == "sink":
    install()

print("hello world")
print("\033[31mred\033[0m \033[48;5;240mBG\033[0m \033[1;97mBOLD\033[0m")
print("中文/emoji: 🔐 ● ▸")

try:
    from rich.console import Console
    from rich.markdown import Markdown
    Console().print(Markdown("# Title\n\n**bold** and `code`\n\n- a\n- b"))
except Exception as e:
    print("rich-skip:", e)

s = get_sink()
# spinner 语义：\r + text + \033[K
s.live_line("  \033[32m⠋\033[0m 执行中 terminal \033[2m1.2s\033[0m")
s.write("after-frame\n")
s.live_line(erase=True)          # \r\033[K
# 思考窗口语义：\033[{n}A\033[J
s.write("t1\nt2\nt3\n")
s.erase_up(3)
s.write("t1\nt2\nt3\n")
s.flush()

_expect_installed = (ui_mode() == "sink")
# 真实 ThinkingDisplay 类（时间固定，输出确定）
from cbhcli_pkg.core import thinking_display as td
td.time.monotonic = lambda: 1000.0
_d = td.ThinkingDisplay(max_lines=8, label="")
_d.enabled = True
_d._get_term_width = lambda: 80
_d._get_term_height = lambda: 24
_d.start_thinking()                     # 💭 思考中... 0s + ...
_d.add_content("第一段内容\nsecond line with 中文")
_d.add_content("\n第三行")
_d.finish_thinking()                    # 💭 思考完毕 0s
_d.cleanup()

print("attrs", sys.stdout.isatty(), os.isatty(sys.stdout.fileno()),
      getattr(sys.stdout, "encoding", "?"),
      "installed-ok" if is_installed() == _expect_installed else "installed-BAD")

sys.stderr.write("STDERR-LINE-1\n")
sys.stderr.flush()

if is_installed():
    uninstall()
print("after-uninstall")
"""


def _set_winsize(fd: int, rows: int = 24, cols: int = 80) -> None:
    try:
        fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
    except Exception:
        pass


def _read_all(fd: int) -> bytes:
    chunks = []
    while True:
        try:
            r, _, _ = select.select([fd], [], [], 5.0)
            if not r:
                break
            data = os.read(fd, 65536)
            if not data:
                break
            chunks.append(data)
        except OSError:
            break
    return b"".join(chunks)


def run_mode(mode: str) -> tuple[bytes, bytes]:
    master, slave = pty.openpty()
    _set_winsize(slave)
    env = os.environ.copy()
    env["CBHCLI_UI"] = mode
    env["TERM"] = "xterm-256color"
    env["COLUMNS"] = "80"
    env["LINES"] = "24"
    env["PYTHONPATH"] = ROOT + os.pathsep + env.get("PYTHONPATH", "")
    p = subprocess.Popen(
        [sys.executable, "-c", CHILD],
        stdin=subprocess.DEVNULL,
        stdout=slave,
        stderr=subprocess.PIPE,
        env=env,
        cwd=ROOT,
    )
    os.close(slave)
    out = _read_all(master)
    os.close(master)
    err = p.stderr.read() if p.stderr else b""
    p.wait(timeout=20)
    return out, err


def _first_diff(a: bytes, b: bytes) -> str:
    n = min(len(a), len(b))
    for i in range(n):
        if a[i] != b[i]:
            lo = max(0, i - 40)
            return (
                f"首个差异 @ {i}\n  legacy={a[lo:i + 40]!r}\n  sink  ={b[lo:i + 40]!r}"
            )
    if len(a) != len(b):
        lo = max(0, n - 40)
        return (
            f"长度不同（legacy={len(a)} / sink={len(b)}）\n"
            f"  尾部 legacy={a[lo:]!r}\n  尾部 sink  ={b[lo:]!r}"
        )
    return ""


def unit_checks() -> list[str]:
    """语义 API 的原始序列 + 属性透传 + close 安全"""
    from cbhcli_pkg.core.ui.sink import SinkWriter, get_sink, install, uninstall

    fails: list[str] = []
    buf = io.StringIO()
    w = SinkWriter(buf)

    w.live_line("X")
    if buf.getvalue() != "\rX\x1b[K":
        fails.append(f"live_line 序列错误: {buf.getvalue()!r}")
    buf.seek(0); buf.truncate(0)

    w.live_line(erase=True)
    if buf.getvalue() != "\r\x1b[K":
        fails.append(f"live_line(erase) 序列错误: {buf.getvalue()!r}")
    buf.seek(0); buf.truncate(0)

    w.erase_up(3)
    if buf.getvalue() != "\x1b[3A\x1b[J":
        fails.append(f"erase_up 序列错误: {buf.getvalue()!r}")
    buf.seek(0); buf.truncate(0)

    if w.write("abc") != 3:
        fails.append("write 返回值不是真实流的返回值")

    # close 不得关闭真实流
    w.close()
    if buf.closed:
        fails.append("close() 关闭了真实流（禁止）")

    # 未安装时 get_sink 绑定当前 stdout
    old = sys.stdout
    try:
        probe = io.StringIO()
        sys.stdout = probe
        get_sink().write("Z")
        if probe.getvalue() != "Z":
            fails.append("未安装时 get_sink() 未绑定当前 sys.stdout")
    finally:
        sys.stdout = old

    # 安装/卸载幂等（用独立流，最后恢复真实 stdout）
    probe_stream = io.StringIO()
    try:
        w1 = install(stream=probe_stream)
        w2 = install(stream=io.StringIO())  # 幂等：仍返回同一实例
        if w1 is not w2:
            fails.append("install() 非幂等（重复安装创建了新实例）")
        uninstall()
        if sys.stdout is not probe_stream:
            fails.append("uninstall 未恢复到安装时的流")
        uninstall()  # 再次 uninstall 应无副作用
    finally:
        sys.stdout = old
    if sys.stdout is not old:
        fails.append("测试结束后 sys.stdout 未恢复")

    return fails


def main() -> int:
    print("=" * 70)
    print("P1 门禁：SINK 透传逐字节一致性")
    print("=" * 70)

    fails: list[str] = []
    fails.extend(unit_checks())

    legacy_out, legacy_err = run_mode("legacy")
    sink_out, sink_err = run_mode("sink")

    if legacy_out != sink_out:
        fails.append("stdout 字节不一致：\n" + _first_diff(legacy_out, sink_out))
    if legacy_err != sink_err:
        fails.append("stderr 字节不一致（N-08 要求 stderr 不受影响）")

    print(f"stdout: legacy={len(legacy_out)}B  sink={len(sink_out)}B  "
          f"{'一致' if legacy_out == sink_out else '不一致'}")
    print(f"stderr: legacy={len(legacy_err)}B  sink={len(sink_err)}B  "
          f"{'一致' if legacy_err == sink_err else '不一致'}")
    print(f"stderr 内容示例: {legacy_err[:60]!r}")

    if fails:
        print("\n❌ FAIL")
        for f in fails:
            print(" -", f)
        return 1
    print("\n✅ PASS（stdout/stderr 逐字节一致；语义 API 与透传属性正常）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
