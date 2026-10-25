"""输出统一入口（SINK）— P1：透传模式

契约位置：`docs/全屏重构行为契约.md`
  - H-05  rich `Console()` 不得显式传 `file=`（靠 sys.stdout 动态获取）
  - D2-08 spinner：主区底部固定活动行
  - T-11  `\\r` / `\\033[K` 覆盖语义
  - N-08  stderr 不接入 SINK

## P1 的目标：**字节级零变化**

本阶段只做一件事：把 `sys.stdout` 换成一个透明代理，所有写入原样转发；
同时给三个"活体组件"（spinner / 思考窗口 / 未来的进度行）提供**语义 API**：

    sink.live_line(text)      -> "\\r" + text + "\\033[K"     （单行覆盖刷新）
    sink.live_line(erase=True)-> "\\r\\033[K"                  （擦除该行）
    sink.erase_up(n)          -> "\\033[{n}A\\033[J"           （上移 n 行并清除）

P1 阶段这些语义方法与原始手写序列**逐字节等价**，因此：
  1. 现有界面、颜色、顺序、空行完全不变；
  2. 后续阶段只要替换 SinkWriter 的内部实现（改为逻辑行模型），
     组件侧代码无需再改。

## 用法

    from cbhcli_pkg.core.ui.sink import install, uninstall, get_sink

    install()          # 进入 CLI 主流程时（仅 CBHCLI_UI != legacy）
    try:
        ...
    finally:
        uninstall()

    # 组件侧（无论是否安装都能用）
    sink = get_sink()
    sink.live_line("  ⠋ 执行中 terminal 1.2s")
    sink.erase_up(3)

## 注意

- `write()` 返回真实流的返回值；`flush()` 透传。
- `fileno()` / `isatty()` / `encoding` / `buffer` 全部透传，保证
  `os.get_terminal_size`、rich、pygments、subagent 的线程代理都正常工作。
- `close()` **不关闭**真实 stdout（避免误伤父进程/测试框架）。
- 线程安全：内部 `RLock`（spinner 线程与主线程会并发写）。
"""

from __future__ import annotations

import atexit
import os
import sys
import threading
from contextlib import nullcontext
from typing import Optional, TextIO

__all__ = [
    "SinkWriter",
    "install",
    "uninstall",
    "is_installed",
    "get_sink",
    "ui_mode",
    "UI_MODES",
]

# 合法的 CBHCLI_UI 档位（P1 只实现 legacy / sink，其余按 legacy 处理）
UI_MODES = ("legacy", "sink", "hybrid", "fullscreen")

_installed: bool = False
_original: Optional[TextIO] = None
_current: Optional["SinkWriter"] = None


def ui_mode() -> str:
    """读取 CBHCLI_UI 环境变量

    v5.5.0 起默认 **fullscreen**（全屏 TUI）；需要旧的内联界面时用
    `CBHCLI_UI=legacy`。非法值也回退到默认值。
    """
    raw = (os.environ.get("CBHCLI_UI") or "").strip().lower()
    return raw if raw in UI_MODES else "fullscreen"


class SinkWriter:
    """透明输出代理（P1：逐字节透传）

    - 不是 `io.TextIOBase` 子类，避免第三方库做 isinstance 判断时的副作用；
    - 所有未知属性透传给真实流（`buffer`/`encoding`/`newlines` 等）。
    """

    __slots__ = ("_real", "_lock", "_mode")

    def __init__(self, real: TextIO, mode: str = "passthrough"):
        self._real = real
        self._lock = threading.RLock()
        self._mode = mode

    # ------------------------------------------------------------------
    #  基础写
    # ------------------------------------------------------------------
    def write(self, s: str) -> int:
        if not s:
            return 0
        with self._lock:
            return self._real.write(s)

    def writelines(self, lines) -> None:
        with self._lock:
            for line in lines:
                self._real.write(line)

    def flush(self) -> None:
        with self._lock:
            try:
                self._real.flush()
            except Exception:
                pass

    # ------------------------------------------------------------------
    #  语义 API（P1 与原始序列逐字节等价）
    # ------------------------------------------------------------------
    def live_line(self, text: Optional[str] = None, erase: bool = False) -> None:
        """单行覆盖刷新（spinner / 进度状态行）

        若底层是 `ScreenSink`（全屏模式），委托给它——它会把这些行标记为
        **临时区域**，不进 transcript（S-11）。
        """
        fn = getattr(self._real, "live_line", None)
        if callable(fn):
            return fn(text, erase=erase)
        if erase or text is None:
            self.write("\r\033[K")
        else:
            self.write("\r" + text + "\033[K")

    def erase_up(self, n: int) -> None:
        """上移 n 行并清除到屏幕末尾（思考窗口重绘）"""
        fn = getattr(self._real, "erase_up", None)
        if callable(fn):
            return fn(n)
        if n > 0:
            self.write(f"\033[{n}A\033[J")

    def transient(self):
        """临时区域上下文（全屏时由 ScreenSink 提供；否则为空操作）"""
        fn = getattr(self._real, "transient", None)
        if callable(fn):
            return fn()
        return nullcontext()

    def atomic(self):
        """批量写入上下文（全屏时由 ScreenSink 提供；否则为空操作）

        其内的多次 write 不触发重绘，退出时只重绘一次 —— 用于
        "擦除旧窗口 + 写新窗口" 这种必须原子呈现的组合（防中间帧闪烁）。
        """
        fn = getattr(self._real, "atomic", None)
        if callable(fn):
            return fn()
        return nullcontext()

    # ------------------------------------------------------------------
    #  透传属性
    # ------------------------------------------------------------------
    @property
    def mode(self) -> str:
        return self._mode

    @property
    def real(self) -> TextIO:
        """真实输出流（供需要 fd 的组件使用）"""
        return self._real

    def fileno(self) -> int:
        return self._real.fileno()

    def isatty(self) -> bool:
        try:
            return bool(self._real.isatty())
        except Exception:
            return False

    def writable(self) -> bool:
        return True

    def readable(self) -> bool:
        return False

    def seekable(self) -> bool:
        return False

    @property
    def closed(self) -> bool:
        return False

    def close(self) -> None:
        """故意不关闭真实流（stdout 归进程所有）"""
        return None

    def __getattr__(self, name):
        # 用 __dict__ 访问，避免 __getattr__ 递归
        return getattr(object.__getattribute__(self, "_real"), name)


def install(stream: Optional[TextIO] = None, mode: str = "passthrough") -> SinkWriter:
    """安装 SINK（幂等）。返回当前 SinkWriter。"""
    global _installed, _original, _current
    if _installed and _current is not None:
        return _current

    real = stream if stream is not None else sys.stdout
    _original = real
    _current = SinkWriter(real, mode=mode)
    sys.stdout = _current  # type: ignore[assignment]
    _installed = True
    atexit.register(uninstall)
    return _current


def uninstall() -> None:
    """卸载 SINK 并恢复真实 stdout（幂等、异常安全）"""
    global _installed, _original, _current
    if not _installed:
        return
    try:
        if _current is not None and sys.stdout is _current and _original is not None:
            sys.stdout = _original  # type: ignore[assignment]
    finally:
        _installed = False
        _original = None
        _current = None


def is_installed() -> bool:
    return _installed and _current is not None


def get_sink() -> SinkWriter:
    """获取当前可用的 SINK

    - 已安装：返回全局实例（稳定，供 spinner 在 `__enter__` 捕获）；
    - 未安装：返回一个绑定**当前** `sys.stdout` 的临时实例。

    注意：未安装时每次调用都会新建对象（很轻），因此组件必须在
    "需要固定输出目标"的时刻调用一次并保存（如 spinner 的 `__enter__`）。
    """
    if _installed and _current is not None:
        return _current
    return SinkWriter(sys.stdout, mode="passthrough")
