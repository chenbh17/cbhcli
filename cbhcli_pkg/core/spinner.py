"""终端加载动画 — 工具执行 spinner

工具执行期间在独立守护线程中绘制 braille 旋转动画 + 已耗时，
主线程保持执行工具（Ctrl+C 语义不变）。动画线程只负责打印，
与主线程的输出通过互斥锁协调，结束时清除动画行。

用法：
    with Spinner("执行中 edit"):
        result = do_work()
    # 退出时动画行自动清除，可接着打印结果
"""
from __future__ import annotations

import sys
import threading
import time

from cbhcli_pkg.core.ui.sink import get_sink

# 纯 braille 圆点帧（U+2800 盲文区，终端字体普遍支持）。
# 之前混入的 ⦦⦯（U+29A6/29AF 数学符号区）在部分字体中无字形，
# 会显示为豆腐方块，导致"转圈和方框轮流出现"。
FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
_INTERVAL = 0.08  # 帧间隔（秒）


class Spinner:
    """上下文管理器形式的终端 spinner（线程安全，异常安全）"""

    def __init__(self, message: str = "执行中", color: str = "\033[36m",
                 dim: str = "\033[2m", reset: str = "\033[0m",
                 enabled: bool = True):
        self.message = message
        self._color = color
        self._dim = dim
        self._reset = reset
        self._enabled = enabled and self._is_tty()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._drawn = False
        # 输出目标：在 __enter__ 时捕获（见下方注释）。
        # 通过 SINK 的语义 API 输出，P1 阶段与旧的 "\r…\033[K" 逐字节等价。
        self._sink = None

    @staticmethod
    def _is_tty() -> bool:
        try:
            return sys.stdout.isatty()
        except Exception:
            return False

    def _render(self):
        start = time.monotonic()
        idx = 0
        while not self._stop_event.is_set():
            elapsed = time.monotonic() - start
            frame = FRAMES[idx % len(FRAMES)]
            with self._lock:
                self._sink.live_line(
                    f"  {self._color}{frame}{self._reset} "
                    f"{self.message} {self._dim}{elapsed:.1f}s{self._reset}"
                )
                self._sink.flush()
                self._drawn = True
            idx += 1
            self._stop_event.wait(_INTERVAL)

    def __enter__(self):
        if self._enabled:
            # 进入时捕获输出目标（此时 sys.stdout 尚未被 python_tool 替换）。
            # get_sink() 在已安装时返回全局 SINK；未安装时绑定当前 stdout。
            self._sink = get_sink()
            self._thread = threading.Thread(target=self._render, daemon=True)
            self._thread.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.stop()
        return False

    def stop(self):
        """停止动画并清除动画行"""
        if not self._enabled:
            return
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=0.5)
        with self._lock:
            if self._drawn and self._sink is not None:
                self._sink.live_line(erase=True)
                self._sink.flush()
                self._drawn = False

    def elapsed(self) -> float:
        return 0.0
