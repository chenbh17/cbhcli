"""全屏 Shell（P3）：主区 + 输入区 + 状态栏

契约位置：`docs/全屏重构行为契约.md`
  - D5 输入区（复用同一个 Style / KeyBindings / Completer）
  - D6 底部状态栏（`_build_toolbar()` 片段直接喂进来）
  - T-13 应用内滚动（PgUp/PgDn、Ctrl+Home/End、stickBottom）
  - D2-08 活动行（SINK 的 `live_line` 落在主区最后一行）
  - D6-14 上下文占用条（由 status_provider 生成，外壳不关心内容）

本模块只负责"屏幕"，不依赖 `CBHCLIApp`：
    - `FullScreenUI.feed(text)`：把 ANSI 文本写入逻辑屏幕（线程安全）
    - `FullScreenUI.set_status(provider)`：状态栏片段（(style, text) 列表）
    - `FullScreenUI.install_sink()/uninstall_sink()`：接管 sys.stdout
    - `on_submit(text)` / `on_interrupt()`：由上层注入

设计要点：
  1. 输入区**不是** PromptSession，而是同一个 `Buffer` + 同一份 KeyBindings/Style；
     Enter/Ctrl+J/Alt+Enter/Ctrl+R/Shift+Tab 仍由项目绑定处理（外部传入）。
  2. 主区只渲染"当前可见的 N 行"（自行算折行），滚动用 offset 控制；
     resize 时只换宽度重算，天然满足 T-17。
  3. 所有写入走 `Screen`，`\\r` / `\\033[K` / `\\033[A` 等语义由 Screen 处理
     （spinner、process 状态行、思考窗口都能直接工作）。
"""

from __future__ import annotations

import asyncio
import math
import os
import sys
import threading
import time
from contextlib import contextmanager
from typing import Callable, List, Optional, Sequence, Tuple

from prompt_toolkit.application import Application, get_app
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.filters import Condition, has_focus
from prompt_toolkit.history import InMemoryHistory
from prompt_toolkit.key_binding import KeyBindings, KeyBindingsBase, merge_key_bindings
from prompt_toolkit.formatted_text import ANSI, to_formatted_text
from prompt_toolkit.layout import (
    ConditionalContainer,
    Float,
    FloatContainer,
    HSplit,
    Layout,
    VSplit,
    Window,
)
from prompt_toolkit.layout.controls import BufferControl, FormattedTextControl
from prompt_toolkit.layout.dimension import Dimension
from prompt_toolkit.layout.menus import CompletionsMenu
from prompt_toolkit.layout.processors import ConditionalProcessor, PasswordProcessor
from prompt_toolkit.mouse_events import MouseEventType
from prompt_toolkit.output import ColorDepth
from prompt_toolkit.styles import Style

from cbhcli_pkg.core.input_box import CHAT_INPUT_STYLE_DICT
from cbhcli_pkg.core.text_width import display_width
from cbhcli_pkg.core.ui.ansi import style_to_pt_style
from cbhcli_pkg.core.ui.lines import Screen

__all__ = ["ScreenSink", "FullScreenUI", "set_active_ui", "get_active_ui"]

StatusFragments = List[Tuple[str, str]]

# 当前活动的全屏 UI（供 input_box / prompt_utils 判断是否需要走 SUSPEND）
_ACTIVE_UI: Optional["FullScreenUI"] = None


def set_active_ui(ui: Optional["FullScreenUI"]) -> None:
    global _ACTIVE_UI
    _ACTIVE_UI = ui


def get_active_ui() -> Optional["FullScreenUI"]:
    return _ACTIVE_UI


class _TranscriptWriter:
    """已提交行的纯文本日志（契约 S-11）

    - 目录 0700 / 文件 0600；
    - 逐行追加，64 行或 500ms flush；
    - 任何异常 → 本会话内禁用（不反复报错）。
    """

    def __init__(self, path: str):
        self.path = path
        self._lock = threading.Lock()
        self._buf: List[str] = []
        self._last_flush = time.monotonic()
        self._fh = None
        self._failed = False
        try:
            d = os.path.dirname(path)
            os.makedirs(d, mode=0o700, exist_ok=True)
            try:
                os.chmod(d, 0o700)
            except Exception:
                pass
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            self._fh = os.fdopen(fd, "w", encoding="utf-8", errors="replace")
            try:
                os.chmod(path, 0o600)
            except Exception:
                pass
        except Exception:
            self._failed = True

    def write(self, line: str) -> None:
        if self._failed or self._fh is None:
            return
        with self._lock:
            self._buf.append(line)
            now = time.monotonic()
            if len(self._buf) >= 64 or now - self._last_flush >= 0.5:
                self._flush_locked()

    def _flush_locked(self) -> None:
        if not self._buf or self._fh is None:
            return
        try:
            self._fh.write("\n".join(self._buf) + "\n")
            self._fh.flush()
            self._buf = []
            self._last_flush = time.monotonic()
        except Exception:
            self._failed = True

    def flush(self) -> None:
        with self._lock:
            self._flush_locked()

    def close(self) -> None:
        with self._lock:
            self._flush_locked()
            try:
                if self._fh is not None:
                    self._fh.close()
            except Exception:
                pass
            self._fh = None


class ScreenSink:
    """把输出喂给 `Screen` 的 SINK（替代 sys.stdout）

    与 `core/ui/sink.SinkWriter` 接口一致；`live_line` / `erase_up` 通过
    `feed()` 交给 Screen 解释，因此 P3 起 spinner/思考窗口的"相对擦除"
    都变成了模型内的覆盖操作。
    """

    def __init__(self, ui: "FullScreenUI", real):
        self._ui = ui
        self._real = real
        self._lock = threading.RLock()

    #: 擦除语义标记：True = `erase_up(n)` 移动的是**逻辑行**（与终端宽度无关）。
    #: 思考窗口据此判断"收窄时可以安全擦除"，不再保留一次性快照（v5.5.0 修复）。
    logical_erase = True

    # --- 基础写 ---
    def write(self, s: str) -> int:
        if not s:
            return 0
        with self._lock:
            self._ui.feed(s)
        return len(s)

    def writelines(self, lines) -> None:
        for line in lines:
            self.write(line)

    def flush(self) -> None:
        return None

    # --- 语义 API ---
    def transient(self):
        """临时区域标记（不进 transcript）"""
        return self._ui.transient()

    def atomic(self):
        """批量写入（期间不重绘，退出时只重绘一次 → 无中间帧闪烁）"""
        return self._ui.atomic()

    def live_line(self, text: Optional[str] = None, erase: bool = False) -> None:
        with self._ui.transient():
            if erase or text is None:
                self.write("\r\033[K")
            else:
                self.write("\r" + text + "\033[K")

    def erase_up(self, n: int) -> None:
        if n > 0:
            with self._ui.transient():
                self.write(f"\033[{n}A\033[J")

    def available_main_lines(self) -> int:
        """主区当前可显示的行数（供思考窗按可视高度收缩窗口）"""
        try:
            rows, cols = self._ui._size()
            return max(1, self._ui._main_height(rows, cols))
        except Exception:
            return 0

    # --- 透传 ---
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
        return None

    def __getattr__(self, name):
        return getattr(object.__getattribute__(self, "_real"), name)


def _pt_width(text: str) -> int:
    """按 prompt_toolkit 的宽度口径估算（emoji/组合字符取较宽者，宁可多留行）"""
    try:
        from prompt_toolkit.utils import get_cwidth
        return max(display_width(text), get_cwidth(text))
    except Exception:
        return display_width(text)


def _fs_debug(msg: str) -> None:
    """诊断日志（CBHCLI_FS_DEBUG=1 时写 /tmp/cbhcli_fs_debug.log）"""
    if not os.environ.get("CBHCLI_FS_DEBUG"):
        return
    try:
        with open("/tmp/cbhcli_fs_debug.log", "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%H:%M:%S')} {msg}\n")
    except Exception:
        pass


#: 「运行中」动画帧（braille，宽度恒为 1；终端字体覆盖稳定）
_SPIN_FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
_SPIN_INTERVAL = 0.1      # 每帧间隔（秒）

#: 鼠标操作后的「重绘静默期」（秒）
#:
#: 终端收到应用输出时会刷新缓冲区，**原生选区（Shift+拖动选中）随之被清除**。
#: 用户实测："选中后鼠标稍微动一下，选中的内容就没了"——本质是点击/释放
#: 触发的重绘（状态栏动画、思考窗刷新、轮末修格）把选区冲掉。
#: 这里在鼠标事件后短暂推迟重绘，让选区稳定下来。
_MOUSE_QUIET_SEC = 0.6
#: 按住左键（可能在拖选）时最长静默时间（秒）——超过后恢复重绘，避免输出卡死
_MOUSE_QUIET_MAX = 3.0

#: 用户手动滚动后，长时间无操作自动回到底部的秒数（v5.5.1）
_IDLE_SCROLL_RESET_SEC = 25.0

#: 关 any-motion（1003）+ 关 urxvt 编码（1015）→ 重发 1000（点击/滚轮）+ 1006（SGR 编码）
#: ⚠️ xterm.js 的 DECRST 1003 会把整个鼠标协议置为 NONE，所以必须随后重发
_MOUSE_RESTRICT_SEQ = "\033[?1003l\033[?1015l\033[?1000h\033[?1006h"


class _WheelWindow(Window):
    """可滚轮滚动的窗口（主区 / 输入区）

    响应鼠标滚轮 -> 主区历史滚动；其余事件返回 NotImplemented，
    交给终端/prompt_toolkit 默认处理（选中由终端原生完成：按住 Shift 拖动）。
    """

    def __init__(self, content=None, *, scroll_cb=None, enabled_cb=None,
                 on_any_mouse=None, **kwargs):
        super().__init__(content, **kwargs)
        self._scroll_cb = scroll_cb
        self._enabled_cb = enabled_cb
        self._on_any_mouse = on_any_mouse

    def _mouse_handler(self, mouse_event):
        _fs_debug(f"mouse {mouse_event.event_type} {mouse_event.position} "
                  f"enabled={self._enabled_cb() if self._enabled_cb else None}")
        if self._on_any_mouse is not None:
            try:
                self._on_any_mouse(mouse_event)
            except Exception:
                pass
        if self._enabled_cb is not None and not self._enabled_cb():
            return NotImplemented
        et = mouse_event.event_type
        if et == MouseEventType.SCROLL_UP:
            if self._scroll_cb is not None:
                self._scroll_cb(3)
                return None
            return NotImplemented
        if et == MouseEventType.SCROLL_DOWN:
            if self._scroll_cb is not None:
                self._scroll_cb(-3)
                return None
            return NotImplemented
        return NotImplemented


class FullScreenUI:
    """全屏三层布局（主区 / 输入区 / 状态栏）"""

    def __init__(
        self,
        *,
        status_provider: Optional[Callable[[], StatusFragments]] = None,
        key_bindings: Optional[KeyBindingsBase] = None,
        completer=None,
        on_submit: Optional[Callable[[str], None]] = None,
        on_interrupt: Optional[Callable[[], None]] = None,
        max_lines: int = 5000,
        style_dict: Optional[dict] = None,
    ):
        self.on_submit = on_submit
        self.on_interrupt = on_interrupt

        self._lock = threading.RLock()
        self._transient_depth = 0  # >0 时提交的行不写 transcript（思考/spinner/活动行）
        self._atomic_depth = 0     # >0 时 feed 不重绘（批量写入，退出时统一重绘）
        self._transcript = None
        self.transcript_path: Optional[str] = None
        self.screen = Screen(max_lines=max_lines, on_commit=self._on_commit)
        # 折行结果缓存 (screen_id, version, width, lines)：
        # 「运行中」动画 10 次/秒重绘，长会话每帧全量折行会白烧 CPU
        self._vlines_cache = None
        self._offset = 0           # 距离底部的行数（0=贴底/跟随最新）
        self._last_total = None     # 上一帧主区逻辑行数（用于"保持阅读位置"）
        self._last_total_cols = 0   # 上一帧折行宽度（宽度变化时不补偿 offset）
        self._idle_scroll_timer = None  # 长时间未滚动 → 自动回到底部
        self._busy = False         # 请求进行中（A1：Enter 不提交）
        # 「运行中」动画（状态栏 spinner）
        self._spin_idx = 0
        self._spin_stop: Optional[threading.Event] = None
        self._spin_thread: Optional[threading.Thread] = None
        # 鼠标捕获（滚轮滚动历史）：唯一模式，始终开启
        self._mouse_enabled = True
        self._last_dbg_rows = None   # 调试：上次记录的"渲染模型输入行"（去重）
        self._mouse_down = False     # 左键按下中（整屏重绘避让）
        self._mouse_down_ts = 0.0    # 左键按下的时刻（拖选静默期上限）
        self._mouse_ts = 0.0         # 最近一次鼠标事件时间
        self._defer_timer = None     # 静默期内推迟重绘的定时器（去重）

        self._status_provider = status_provider or (lambda: [])
        _style_dict = dict(style_dict or CHAT_INPUT_STYLE_DICT)
        # 「运行中」标识（请求/命令执行期间显示在状态栏最左侧）
        _style_dict.setdefault("running", "bold #5fd7ff")
        self.style = Style.from_dict(_style_dict)

        # 输入缓冲（与 PromptSession 同款参数）
        self.buffer = Buffer(
            multiline=True,
            completer=completer,
            complete_while_typing=True,
            history=InMemoryHistory(),
            accept_handler=self._accept,
            on_text_changed=lambda _buf: self._debug_input_line("change"),
        )

        # 本项目键位 + 外壳键位（外壳在后 → 优先级更高）
        self._own_bindings = KeyBindings()
        self._setup_bindings()
        merged = (
            merge_key_bindings([key_bindings, self._own_bindings])
            if key_bindings is not None
            else self._own_bindings
        )

        self._main_window = _WheelWindow(
            FormattedTextControl(self._main_fragments),
            height=Dimension(weight=1),
            wrap_lines=False,
            scroll_cb=self._scroll,
            enabled_cb=lambda: self._mouse_enabled,
            on_any_mouse=self._on_mouse_activity,
        )
        self._input_control = BufferControl(buffer=self.buffer)
        self._input_window = _WheelWindow(
            self._input_control,
            height=self._input_dimension,
            get_line_prefix=self._line_prefix,
            wrap_lines=True,
            scroll_cb=self._scroll,
            enabled_cb=lambda: self._mouse_enabled,
        )
        self._status_window = Window(
            FormattedTextControl(self._status_fragments),
            style="class:bottom-toolbar",
            height=Dimension(min=1, max=3),
            dont_extend_height=True,
        )

        # 应用内模态（P5b）：替换输入区显示"提示行 + 单行输入"
        self._modal_active = False
        self._modal_password = False
        self._modal_future: Optional[asyncio.Future] = None
        self._modal_prompt_frags = []
        self._modal_buffer = Buffer(
            multiline=False,
            accept_handler=self._modal_accept,
        )
        self._modal_control = BufferControl(
            buffer=self._modal_buffer,
            input_processors=[
                ConditionalProcessor(
                    # 契约 D7-05：不显示任何占位符（连 * 都不显示）——
                    # char="" 让输入区保持空白，光标停在提示串之后
                    PasswordProcessor(char=""),
                    Condition(lambda: self._modal_password),
                )
            ],
        )
        self._modal_window = Window(
            self._modal_control,
            height=Dimension(min=1),
            # 提示语与输入在同一行（与旧 PromptSession 一致）：
            # "模型名称: deepseek"，不再分成"上面提示、下面输入"两块。
            get_line_prefix=self._modal_line_prefix,
            wrap_lines=True,
        )
        self._modal_prompt_window = Window(
            FormattedTextControl(self._modal_prompt_fragments),
            height=Dimension(min=1),
            wrap_lines=True,
        )
        # 提示过长放不下时才保留独立提示行；普通提问全部走内联前缀。
        modal_body = HSplit([
            ConditionalContainer(
                self._modal_prompt_window,
                ~Condition(self._modal_inline_prompt),
            ),
            self._modal_window,
        ])

        body = FloatContainer(
            HSplit([
                self._main_window,
                ConditionalContainer(self._input_window, ~Condition(lambda: self._modal_active)),
                ConditionalContainer(modal_body, Condition(lambda: self._modal_active)),
                self._status_window,
            ]),
            floats=[
                Float(
                    xcursor=True,
                    ycursor=True,
                    transparent=True,
                    content=CompletionsMenu(
                        max_height=12,
                        scroll_offset=1,
                        display_arrows=True,
                        extra_filter=has_focus(self.buffer),
                    ),
                )
            ],
        )

        # 最右预留 2 列：任何行都不写终端最后一格（宽字符占 2 列，
        # 只留 1 列时"最后一个内容格是宽字符"仍会压到最后一列 → 终端自动换行/滚屏）。
        # 否则宽字符（CJK/emoji）裁切或恰好写满最后一行时会触发终端自动换行，
        # 在最底部（状态栏）换行 -> 整屏上滚 1 行 -> 输入框错位/退格残留
        # （用户实测：第二轮输入时"最前面有一位退格退不掉"）。
        self._layout_root = VSplit([body, Window(width=2)])

        self.app: Application = Application(
            layout=Layout(self._layout_root),
            style=self.style,
            key_bindings=merged,
            full_screen=True,
            # v5.5.0：开启鼠标事件（滚轮滚动历史）。这是**唯一模式**：
            # 已取消 F2 开关与"不捕获"模式（用户要求），选中文本用 Shift+拖动。
            mouse_support=True,
            color_depth=ColorDepth.DEPTH_8_BIT,
            after_render=self._after_render,
        )

        self._real_stdout = None
        self._sink: Optional[ScreenSink] = None
        self._input_future: Optional[asyncio.Future] = None
        self._mouse_modes_fixed = False

    # ------------------------------------------------------------------
    #  生命周期 / SINK
    # ------------------------------------------------------------------
    def install_sink(self) -> ScreenSink:
        if self._sink is None:
            self._real_stdout = sys.stdout
            self._sink = ScreenSink(self, self._real_stdout)
            sys.stdout = self._sink  # type: ignore[assignment]
            set_active_ui(self)
        return self._sink

    def uninstall_sink(self) -> None:
        try:
            self._stop_spinner()
        except Exception:
            pass
        if self._sink is not None and sys.stdout is self._sink and self._real_stdout is not None:
            sys.stdout = self._real_stdout  # type: ignore[assignment]
        self._sink = None
        if get_active_ui() is self:
            set_active_ui(None)

    def restore_terminal_modes(self) -> None:
        """退出时显式恢复鼠标/终端模式（滚轮、拖选、原生滚动）

        prompt_toolkit 退出已会 `?1049l` 并关闭鼠标上报，但它只复位
        `1000/1003/1006/1015`。这里再显式关掉 **1002（DRAG）** 与
        **1007（alternate scroll）**，避免不同终端/历史版本遗留模式导致
        退出 cbhcli 后**滚轮不能滚动**。
        """
        try:
            real = self._real_stdout or sys.stdout
            real.write("\033[?1000l\033[?1002l\033[?1003l"
                       "\033[?1006l\033[?1015l\033[?1007l\033[?25h")
            real.flush()
        except Exception:
            pass

    def after_start(self) -> None:
        """app 启动后的终端模式微调（幂等；也可由 after_render 自动完成）"""
        self._restrict_mouse_modes(self.app.output)

    def _restrict_mouse_modes(self, output) -> None:
        """关 1003（any-motion）并立刻重发 1000/1006

        ⚠️ xterm.js（JupyterLab 终端）把 **任何一个** 鼠标模式 reset 都实现为
        `activeProtocol = NONE`（DECRST 1003 会把 1000 一起关掉）——所以关掉
        1003 之后必须**重发 1000h/1006h**，否则滚轮彻底失效（用户实测）。
        """
        if self._mouse_modes_fixed:
            return
        self._mouse_modes_fixed = True
        try:
            output.write_raw(_MOUSE_RESTRICT_SEQ)
            _fs_debug("mouse modes: " + _MOUSE_RESTRICT_SEQ.replace("\033[?", "?"))
        except Exception:
            pass

    def _after_render(self, app) -> None:
        """首次渲染后调用（任何嵌入方都会经过，不依赖 app.py 显式调用）"""
        self._restrict_mouse_modes(app.output)
        self._debug_input_line("render")

    # ------------------------------------------------------------------
    #  输入等待（供异步会话循环）
    # ------------------------------------------------------------------
    async def wait_input(self) -> Optional[str]:
        """等待一次提交；app 退出时返回 None"""
        loop = asyncio.get_running_loop()
        self._input_future = loop.create_future()
        try:
            return await self._input_future
        finally:
            self._input_future = None

    def close_input(self) -> None:
        """app 退出时唤醒等待者（主输入 + 模态）"""
        fut = self._input_future
        if fut is not None and not fut.done():
            try:
                fut.set_result(None)
            except Exception:
                pass
        self._cancel_modal()

    # ------------------------------------------------------------------
    #  应用内模态（P5b）：替代 SUSPEND 的旧 PromptSession
    # ------------------------------------------------------------------
    def _format_prompt(self, prompt: str):
        try:
            if "\033[" in prompt or "\x1b[" in prompt:
                return to_formatted_text(ANSI(prompt))
            return [("class:prompt", prompt)]
        except Exception:
            return [("class:prompt", prompt)]

    def _modal_prompt_fragments(self):
        return self._modal_prompt_frags

    def _modal_prompt_width(self) -> int:
        text = "".join(t for _, t in self._modal_prompt_frags)
        return _pt_width(text)

    def _modal_inline_prompt(self) -> bool:
        """提示语能否与输入放在同一行（避免挤掉输入空间）"""
        try:
            cols = self._size()[1]
            return self._modal_prompt_width() + 4 < cols
        except Exception:
            return True

    def _modal_line_prefix(self, line_number: int, wrap_count: int):
        """模态输入的行前缀：首行显示完整提示，续行用等宽空格对齐"""
        if not self._modal_inline_prompt():
            return []
        if line_number == 0 and wrap_count == 0:
            return self._modal_prompt_frags
        width = self._modal_prompt_width()
        return [("class:prompt", " " * width)] if width else []

    def _modal_accept(self, buffer: Buffer) -> bool:
        fut = self._modal_future
        text = buffer.text
        buffer.reset()
        if fut is not None and not fut.done():
            fut.set_result(text)
        return True

    def _cancel_modal(self) -> None:
        fut = self._modal_future
        if fut is not None and not fut.done():
            try:
                fut.set_result(None)
            except Exception:
                pass

    async def _ask_modal(self, prompt: str, password: bool):
        if not self.app.is_running:
            return None
        loop = asyncio.get_running_loop()
        fut = loop.create_future()
        self._modal_future = fut
        self._modal_password = password
        self._modal_prompt_frags = self._format_prompt(prompt)
        self._modal_buffer.reset()
        self._modal_active = True
        try:
            self.app.layout.focus(self._modal_window)
        except Exception:
            pass
        self.invalidate()
        try:
            return await fut
        finally:
            self._modal_active = False
            self._modal_password = False
            self._modal_future = None
            self._modal_buffer.reset()
            try:
                self.app.layout.focus(self._input_window)
            except Exception:
                pass
            self.invalidate()

    def ask_text_sync(self, prompt: str, password: bool = False):
        """供工作线程调用：弹出应用内模态并阻塞等待（取消返回 None）"""
        if not self.app.is_running:
            return None
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            pass  # 正常：工作线程没有运行中的事件循环
        else:
            raise RuntimeError("ask_text_sync 不能从事件循环线程调用")
        loop = getattr(self.app, "loop", None)
        if loop is None or not loop.is_running():
            return None
        fut = asyncio.run_coroutine_threadsafe(
            self._ask_modal(prompt, password), loop)
        return fut.result()

    # ------------------------------------------------------------------
    #  SUSPEND：临时让出全屏，跑旧 PromptSession / getpass（P5b 前兜底）
    # ------------------------------------------------------------------
    async def suspend_run(self, fn):
        """在 alt screen 之外执行阻塞函数（如旧 PromptSession / getpass）

        取自 prompt_toolkit `in_terminal` 的实现，但不依赖 `get_app()` 上下文；
        必须在 app 的事件循环线程调用。
        """
        app = self.app
        if not app.is_running:
            return fn()
        try:
            await app.renderer.wait_for_cpr_responses()
        except Exception:
            pass
        app.renderer.erase()
        app._running_in_terminal = True
        try:
            with app.input.detach():
                with app.input.cooked_mode():
                    loop = asyncio.get_running_loop()
                    return await loop.run_in_executor(None, fn)
        finally:
            app._running_in_terminal = False
            try:
                app.renderer.reset()
                app._request_absolute_cursor_position()
                app._redraw()
            except Exception:
                try:
                    app.invalidate()
                except Exception:
                    pass

    def suspend_run_sync(self, fn):
        """供工作线程调用：把 suspend_run 调度到 app 循环并阻塞等待结果"""
        loop = getattr(self.app, "loop", None)
        if loop is None or not loop.is_running():
            return fn()
        fut = asyncio.run_coroutine_threadsafe(self.suspend_run(fn), loop)
        return fut.result()

    # ------------------------------------------------------------------
    #  退出 dump（D0-09）
    # ------------------------------------------------------------------
    def dump_tail(self, n: int = 200) -> int:
        """把最近 n 行渲染内容（带颜色）写到真实 stdout，返回总行数"""
        real = self._real_stdout or sys.stdout
        try:
            cols = real.fileno  # noqa: F841  (仅确认是真流)
            width = 80
            try:
                width = os.get_terminal_size(real.fileno()).columns
            except Exception:
                width = 80
        except Exception:
            width = 80

        from cbhcli_pkg.core.ui.ansi import segments_to_ansi

        with self._lock:
            visual = self.screen.visual_lines(max(20, width))
        total = len(visual)
        start = max(0, total - n)
        for line in visual[start:]:
            real.write(segments_to_ansi(line) + "\n")
        real.flush()
        return total

    def _on_mouse_activity(self, mouse_event) -> None:
        try:
            et = mouse_event.event_type
        except Exception:
            return
        if et in (MouseEventType.SCROLL_UP, MouseEventType.SCROLL_DOWN):
            # 滚轮必须「跟手」：不进入静默期（滚动后立即重绘）
            self._mouse_ts = 0.0
            return
        self._mouse_ts = time.monotonic()
        if et == MouseEventType.MOUSE_DOWN:
            self._mouse_down = True
            self._mouse_down_ts = self._mouse_ts
        elif et in (MouseEventType.MOUSE_UP,):
            self._mouse_down = False

    # --- 选区保护：鼠标操作后短暂"重绘静默"（v5.5.0）-----------------
    def _mouse_quiet_delay(self) -> float:
        """还需要静默多久（秒）；0 = 可以正常重绘

        终端在收到输出时会刷新缓冲区并**清掉原生选区**，所以鼠标操作后
        短暂停止重绘，让用户刚选中的文本稳定（用户实测："选中后鼠标稍微
        动一下选中就没了"）。按住左键（可能在拖选）时持续静默，
        但最多 `_MOUSE_QUIET_MAX` 秒，避免流式输出卡死。
        """
        now = time.monotonic()
        if self._mouse_down:
            if (now - self._mouse_down_ts) < _MOUSE_QUIET_MAX:
                return 0.25          # 稍后再检查（松开后走下面的常规定时）
        ts = self._mouse_ts
        if not ts:
            return 0.0
        return max(0.0, _MOUSE_QUIET_SEC - (now - ts))

    def _defer_invalidate(self, delay: float) -> None:
        """静默期内推迟重绘（只登记一次；到期后若仍在静默则继续顺延）"""
        app = getattr(self, "app", None)
        if app is None:
            return
        loop = app.loop
        if loop is None or not loop.is_running():
            return

        def _fire():
            self._defer_timer = None
            d = self._mouse_quiet_delay()
            if d > 0:
                self._defer_invalidate(d)
                return
            try:
                app.invalidate()
            except Exception:
                pass

        def _schedule():
            if self._defer_timer is not None:
                return
            try:
                self._defer_timer = loop.call_later(max(0.02, delay), _fire)
            except Exception:
                self._defer_timer = None

        try:
            loop.call_soon_threadsafe(_schedule)
        except Exception:
            pass

    def _debug_input_line(self, tag: str) -> None:
        """诊断：记录输入缓冲区与"渲染器模型里的输入行"（CBHCLI_FS_DEBUG=1）"""
        if not os.environ.get("CBHCLI_FS_DEBUG"):
            return
        try:
            scr = getattr(self.app.renderer, "_last_screen", None)
            rows = []
            size = self._size()
            rws, cls = size[0], size[1]
            if scr is not None and rws and cls:
                for y in range(rws):
                    line = scr.data_buffer[y]
                    txt = "".join(line[x].char for x in range(cls))
                    if "❯" in txt:
                        rows.append((y, txt.rstrip()))
            try:
                from prompt_toolkit.utils import get_cwidth
                w_pref = get_cwidth("❯ ")
                w_text = get_cwidth(self.buffer.text)
            except Exception:
                w_pref = w_text = -1
            try:
                size = self._size()
            except Exception:
                size = (0, 0)
            if tag == "render":
                key = tuple(rows)
                if key == self._last_dbg_rows:
                    return
                self._last_dbg_rows = key
            _fs_debug(f"input[{tag}] text={self.buffer.text!r} "
                      f"cur={self.buffer.cursor_position} "
                      f"w(prefix)={w_pref} w(text)={w_text} size={size} rows={rows}")
        except Exception as e:
            _fs_debug(f"input[{tag}] err={e!r}")

    def invalidate(self) -> None:
        # v5.5.0：鼠标操作后的静默期内不重绘——终端收到输出会清掉原生选区，
        # 这正是"选中后鼠标稍微动一下就没了"的原因。静默结束后统一补一次。
        d = self._mouse_quiet_delay()
        if d > 0:
            self._defer_invalidate(d)
            return
        app = getattr(self, "app", None)
        if app is None:
            return
        try:
            loop = app.loop
            if loop is not None and loop.is_running():
                loop.call_soon_threadsafe(app.invalidate)
        except Exception:
            pass

    # ------------------------------------------------------------------
    #  写入 / 状态
    # ------------------------------------------------------------------
    @contextmanager
    def transient(self):
        """临时区域：此间提交的行不写 transcript（思考窗口/spinner/活动行）"""
        with self._lock:
            self._transient_depth += 1
        try:
            yield
        finally:
            with self._lock:
                self._transient_depth = max(0, self._transient_depth - 1)

    def _on_commit(self, text: str) -> None:
        if self._transient_depth > 0:
            return
        t = self._transcript
        if t is not None:
            t.write(text)

    def start_transcript(self, path: Optional[str]) -> None:
        """开始/切换 transcript（None = 关闭；`--no-save` 时传 None）"""
        self.close_transcript()
        self.transcript_path = path
        if path:
            self._transcript = _TranscriptWriter(path)

    def close_transcript(self) -> None:
        if self._transcript is not None:
            try:
                self._transcript.close()
            except Exception:
                pass
            self._transcript = None

    def transcript_display_path(self) -> str:
        p = self.transcript_path
        if not p:
            return ""
        home = os.path.expanduser("~")
        if p == home:
            return "~"
        if p.startswith(home + os.sep):
            return "~" + p[len(home):]
        return p

    def feed(self, text: str) -> None:
        """线程安全：把 ANSI 文本写入逻辑屏幕"""
        with self._lock:
            self.screen.feed(text)
            deferred = self._atomic_depth > 0
        if not deferred:
            self.invalidate()

    @contextmanager
    def atomic(self):
        """批量写入：期间 feed 不重绘，退出时只重绘一次

        思考窗口每次刷新都是"擦掉 9 行 + 重写 9 行"；若中途被渲染线程
        插进去画一帧，就会看到窗口闪一下（v5.5.0 用户反馈的"思考时总闪"）。
        """
        with self._lock:
            self._atomic_depth += 1
        try:
            yield
        finally:
            with self._lock:
                self._atomic_depth = max(0, self._atomic_depth - 1)
                done = self._atomic_depth == 0
            if done:
                self.invalidate()

    def set_status_provider(self, provider: Callable[[], StatusFragments]) -> None:
        self._status_provider = provider
        self.invalidate()

    def set_busy(self, busy: bool) -> None:
        """请求/命令执行状态：驱动状态栏「运行中」动画"""
        busy = bool(busy)
        if busy and not self._busy:
            self._start_spinner()
        elif not busy and self._busy:
            self._stop_spinner()
            # 一轮结束：整屏重绘一次，清掉本轮渲染可能留下的残格
            # （用户实测：不做这一步，下一轮输入行会出现"❯ nnihao"残影）
            self._schedule_full_repaint(0.15)
        self._busy = busy
        self.invalidate()

    def _schedule_full_repaint(self, delay: float = 0.15) -> None:
        """延迟一次「只重绘输入区」的修复（清掉残格，但不动主区）

        背景：
        - 每轮结束后，输入行可能残留上一帧的字符（用户实测 `❯ nnihao`）；
        - 但**不能用 renderer.reset()**：它会退出/重入备用屏 + 关开鼠标上报，
          直接清掉终端选区（用户实测 Shift+拖动被取消）；
        - 所以这里只把「输入区那几行」在上一帧缓存里标脏，下一次渲染只重写这几行，
          主区（你选中的回复内容）一个格子都不动。

        鼠标正在按下 / 刚有鼠标动作时顺延，避免打断拖选。
        """
        app = getattr(self, "app", None)
        if app is None:
            return
        try:
            loop = app.loop
            if loop is None or not loop.is_running():
                return

            def _do():
                try:
                    if getattr(self, "_mouse_down", False):
                        self._schedule_full_repaint(0.6)
                        return
                    if (time.monotonic() - getattr(self, "_mouse_ts", 0.0)) < 0.8:
                        self._schedule_full_repaint(0.6)
                        return
                    self._mark_input_rows_dirty()
                except Exception:
                    pass
                # 统一走 self.invalidate()：鼠标静默期内会被推迟（保护选区）
                self.invalidate()

            loop.call_later(delay, _do)
        except Exception:
            pass

    def _mark_input_rows_dirty(self) -> None:
        """把上一帧缓存里的输入区行标脏（强制重写，主区不动）"""
        from prompt_toolkit.layout.screen import Char

        app = self.app
        scr = getattr(app.renderer, "_last_screen", None)
        rows, cols = self._size()
        top = self._main_height(rows, cols)
        bottom = min(rows, top + self._input_height(rows, cols))
        if scr is None:
            return
        for y in range(max(0, top), max(0, bottom)):
            line = scr.data_buffer[y]
            for x in range(cols):
                c = line[x]
                line[x] = Char(c.char, "[cbhcli-repaint]")
        _fs_debug(f"input rows repaint {top}..{bottom}")

    # --- 「运行中」动画（只重绘状态栏，主区不受影响）-----------------
    def _start_spinner(self) -> None:
        if self._spin_thread is not None and self._spin_thread.is_alive():
            return
        self._spin_stop = threading.Event()
        self._spin_idx = 0
        t = threading.Thread(target=self._spin_loop,
                             args=(self._spin_stop,), daemon=True)
        self._spin_thread = t
        t.start()

    def _stop_spinner(self) -> None:
        ev = self._spin_stop
        self._spin_stop = None
        if ev is not None:
            ev.set()
        self._spin_thread = None
        self._spin_idx = 0

    def _spin_loop(self, stop: "threading.Event") -> None:
        while not stop.wait(_SPIN_INTERVAL):
            with self._lock:
                self._spin_idx = (self._spin_idx + 1) % len(_SPIN_FRAMES)
            # 模态提问期间隐藏动画，不必每 100ms 重绘
            if not self._modal_active:
                self.invalidate()

    @property
    def busy(self) -> bool:
        return self._busy

    def clear_main(self) -> None:
        """Ctrl+L：清空主区视图（transcript 不受影响）"""
        with self._lock:
            self.screen = Screen(max_lines=self.screen.max_lines)
            self._vlines_cache = None
            self._offset = 0
        self.invalidate()

    # ------------------------------------------------------------------
    #  布局尺寸
    # ------------------------------------------------------------------
    def _status_line_count(self) -> int:
        try:
            frags = self._status_provider() or []
        except Exception:
            return 1
        n = 1 + sum(t.count("\n") for _, t in frags)
        return max(1, min(3, n))

    def _input_height(self, rows: int, cols: int) -> int:
        status_h = self._status_line_count()
        space = max(1, rows - status_h - 1)
        # 模态：短提示与输入同一行；长提示才额外占提示行
        if self._modal_active:
            text = "".join(t for _, t in self._modal_prompt_frags)
            if self._modal_inline_prompt():
                total = self._modal_prompt_width() + _pt_width(
                    self._modal_buffer.text)
                lines = max(1, math.ceil(max(1, total) / max(1, cols - 4)))
                return max(1, min(lines, space))
            lines = max(1, math.ceil(max(1, _pt_width(text)) / max(1, cols - 2)))
            return max(2, min(lines + 1, space))
        lower = max(1, min(8, space))
        upper = max(lower, min(max(8, rows // 3), space))
        if not self.buffer.text:
            return lower
        avail_w = max(1, cols - 4)  # 预留右 2 列 + 提示符 "❯ "
        n = 0
        for line in self.buffer.text.split("\n"):
            n += max(1, math.ceil(_pt_width(line) / avail_w))
        return max(lower, min(upper, n))

    def _main_height(self, rows: int, cols: int) -> int:
        return max(1, rows - self._status_line_count() - self._input_height(rows, cols))

    def _input_dimension(self) -> Dimension:
        """输入区高度（min=补全预留 8，max=1/3 屏；随内容增长）"""
        rows, cols = self._size()
        status_h = self._status_line_count()
        space = max(1, rows - status_h - 1)
        lower = max(1, min(8, space))
        upper = max(lower, min(max(8, rows // 3), space))
        return Dimension(min=lower, max=upper, preferred=self._input_height(rows, cols))

    def _visual_lines(self, width: int):
        """按宽度折行（带缓存：内容版本 + 宽度 + Screen 实例）"""
        with self._lock:
            sid = id(self.screen)
            ver = getattr(self.screen, "version", 0)
            c = self._vlines_cache
            if c is not None and c[0] == sid and c[1] == ver and c[2] == width:
                return c[3]
            lines = self.screen.visual_lines(width)
            self._vlines_cache = (sid, ver, width, lines)
            return lines

    def _max_offset(self) -> int:
        try:
            size = get_app().output.get_size()
        except Exception:
            return 0
        total = len(self._visual_lines(max(1, size.columns - 2)))
        return max(0, total - self._main_height(size.rows, size.columns))

    # ------------------------------------------------------------------
    #  渲染
    # ------------------------------------------------------------------
    @staticmethod
    def _fit_status_frags(frags, max_width: int):
        """按显示宽度截断状态栏片段（宽字符安全，逐行处理）

        状态栏在最底部：一旦内容写满终端最后一列（尤其最后一格是宽字符），
        终端会自动换行 → 整屏上滚 → 输入行错位/残格（用户实测 `❯ nnihao`）。
        """
        out = []
        width = 0
        truncated = False
        for style, text in frags:
            buf = []
            for ch in text:
                if ch == "\n":
                    if buf:
                        out.append((style, "".join(buf)))
                        buf = []
                    out.append((style, "\n"))
                    width = 0
                    truncated = False
                    continue
                if truncated:
                    continue
                cw = display_width(ch)
                if width + cw > max_width:
                    truncated = True
                    continue
                buf.append(ch)
                width += cw
            if buf:
                out.append((style, "".join(buf)))
        return out

    def _status_fragments(self) -> StatusFragments:
        try:
            frags = list(self._status_provider() or [])
        except Exception:
            frags = []
        # 「运行中」动画：仅在真正执行请求/命令且**没有模态提问**时显示。
        # /resume、/model、/login 等等待用户输入时不能显示"正在运行"。
        if self._busy and not self._modal_active:
            try:
                frame = _SPIN_FRAMES[self._spin_idx % len(_SPIN_FRAMES)]
            except Exception:
                frame = _SPIN_FRAMES[0]
            frags = [("class:running", f"{frame} 运行中"),
                     ("class:bottom-toolbar.sep", " │ ")] + frags
        # 最底部这一行绝不能写满终端最后一列（宽字符会触发自动换行→整屏上滚→
        # 输入行错位/残格）。按显示宽度截断到 cols-4，宽字符安全。
        try:
            cols = self._size()[1]
            frags = self._fit_status_frags(frags, max(10, cols - 4))
        except Exception:
            pass
        return frags

    def _main_fragments(self):
        try:
            size = get_app().output.get_size()
        except Exception:
            return []
        text_cols = max(1, size.columns - 2)   # 最右 2 列预留
        avail = self._main_height(size.rows, size.columns)
        with self._lock:
            visual = self._visual_lines(text_cols)
            total = len(visual)
            # v5.5.1：滚动跟随
            #  - 贴底（offset==0）：新内容自动跟随（天然，end 始终在末尾）
            #  - 用户上翻查看历史（offset>0）：**内容增长时同步增加 offset**，
            #    屏幕上看到的行保持不动（否则新输出会把阅读位置一路顶上去）
            prev_total = self._last_total
            prev_cols = self._last_total_cols
            if (prev_total is not None and prev_cols == text_cols
                    and total > prev_total and self._offset > 0):
                self._offset = min(max(0, total - avail),
                                   self._offset + (total - prev_total))
            self._last_total = total
            self._last_total_cols = text_cols
            offset = max(0, min(self._offset, max(0, total - avail)))
            end = max(0, total - offset)
            start = max(0, end - avail)
            lines = visual[start:end]

        out: List[Tuple[str, str]] = []
        for i, line in enumerate(lines):
            if i:
                out.append(("", "\n"))
            for text, style in line:
                out.append((style_to_pt_style(style), text))
        return out

    def _line_prefix(self, line_number: int, wrap_count: int):
        if line_number == 0 and wrap_count == 0:
            return [("class:prompt", "❯ ")]
        return [("class:prompt", "  ")]

    # ------------------------------------------------------------------
    #  输入
    # ------------------------------------------------------------------
    def _accept(self, buffer: Buffer) -> bool:
        # 返回 True = 保留文本（不 reset）；False = 由 validate_and_handle 负责
        # reset —— 注意 reset 必须发生在 append_to_history() 之后，否则
        # ↑ 历史会记录成空字符串。
        if self._modal_active:
            return True
        if self._busy:
            return True  # A1：请求期间 Enter 不提交，草稿保留
        text = buffer.text.strip()
        self._debug_input_line("submit")
        if not text:
            self.invalidate()
            return False
        # v5.5.1：发送消息后自动回到最新位置（用户可能正在翻历史）
        self.scroll_to_bottom()
        fut = self._input_future
        if fut is not None and not fut.done():
            fut.set_result(text)
        elif self.on_submit is not None:
            self.on_submit(text)
        self.invalidate()
        return False

    def _setup_bindings(self) -> None:
        kb = self._own_bindings
        buf = self.buffer

        @kb.add("enter")
        def _enter(event):
            if self._modal_active:
                self._modal_buffer.validate_and_handle()
                return
            if self._busy:
                return
            event.current_buffer.validate_and_handle()

        @kb.add("c-j")
        def _newline(event):
            if self._modal_active:
                return
            event.current_buffer.insert_text("\n")

        @kb.add("escape", "enter")
        def _alt_enter(event):
            if self._modal_active:
                return
            event.current_buffer.insert_text("\n")

        @kb.add("escape", filter=Condition(lambda: self._modal_active))
        def _modal_cancel(event):
            self._cancel_modal()

        @kb.add("c-c")
        def _interrupt(event):
            # K-17 优先级 1：模态打开时只取消模态
            if self._modal_active:
                self._cancel_modal()
                return
            if self.on_interrupt is not None:
                self.on_interrupt()
            else:
                event.app.exit()

        @kb.add("c-d", filter=Condition(lambda: has_focus(buf)() and not buf.text))
        def _eof(event):
            if os.environ.get("CBHCLI_FS_DEBUG"):
                print(f"[fs] c-d handler busy={self._busy} modal={self._modal_active}",
                      file=sys.stderr, flush=True)
            if not self._busy:
                event.app.exit()

        @kb.add("pageup")
        def _page_up(event):
            self._scroll(max(1, self._main_height(*self._size()) - 1))

        @kb.add("pagedown")
        def _page_down(event):
            self._scroll(-max(1, self._main_height(*self._size()) - 1))

        @kb.add("c-home")
        def _scroll_top(event):
            # v5.5.1：同样走"长时间无操作自动回底"的计时
            self._scroll(max(1, self._max_offset()))

        @kb.add("c-end")
        def _scroll_bottom(event):
            self.scroll_to_bottom()

        @kb.add("c-l")
        def _clear(event):
            self.clear_main()

    def _size(self) -> Tuple[int, int]:
        try:
            size = get_app().output.get_size()
            return size.rows, size.columns
        except Exception:
            return 24, 80

    def text_width(self) -> int:
        """主区可用文字宽度（终端列数 − 右侧预留 2 列）

        v5.5.0：外部渲染器（rich 表格 / 代码预览 / 进度条等）必须按这个
        宽度出图。若按终端满宽（columns）渲染，最右 1~2 列会被主区的
        折行逻辑折到下一行最左侧——表现为 edit 差异预览"右侧绿色换行到
        左边、红绿错位"（用户实测）。
        """
        try:
            return max(20, self._size()[1] - 2)
        except Exception:
            return 78

    def _scroll(self, delta: int) -> None:
        """滚动主区（delta>0 = 往历史方向）并重置"自动回底部"计时"""
        self._offset = max(0, min(self._max_offset(), self._offset + delta))
        self._arm_idle_scroll_reset()
        self.invalidate()

    def _arm_idle_scroll_reset(self) -> None:
        """用户滚动后，若长时间无操作则自动回到底部（v5.5.1）

        需求：手动滚动时保持用户位置（方便浏览）；但长时间不操作应回到
        最新，避免"发完消息看不到新内容"。
        """
        if self._offset <= 0:
            # 已经贴底，不需要计时
            if self._idle_scroll_timer is not None:
                try:
                    self._idle_scroll_timer.cancel()
                except Exception:
                    pass
                self._idle_scroll_timer = None
            return
        app = getattr(self, "app", None)
        loop = getattr(app, "loop", None) if app is not None else None
        if loop is None or not loop.is_running():
            return
        if self._idle_scroll_timer is not None:
            try:
                self._idle_scroll_timer.cancel()
            except Exception:
                pass

        def _fire():
            self._idle_scroll_timer = None
            if self._offset > 0:
                self._offset = 0
                self.invalidate()

        def _schedule():
            try:
                self._idle_scroll_timer = loop.call_later(
                    _IDLE_SCROLL_RESET_SEC, _fire)
            except Exception:
                self._idle_scroll_timer = None

        try:
            loop.call_soon_threadsafe(_schedule)
        except Exception:
            pass

    def scroll_to_bottom(self) -> None:
        """回到最新位置（发送消息 / 用户显式操作时调用）"""
        if self._idle_scroll_timer is not None:
            try:
                self._idle_scroll_timer.cancel()
            except Exception:
                pass
            self._idle_scroll_timer = None
        if self._offset:
            self._offset = 0
        self.invalidate()

    # ------------------------------------------------------------------
    #  运行
    # ------------------------------------------------------------------
    def run(self, **kwargs):
        """同步入口（demo/测试用）"""
        self.install_sink()
        try:
            return self.app.run(**kwargs)
        finally:
            self.uninstall_sink()

    async def run_async(self, **kwargs):
        """异步入口（demo/测试用；调用方负责在内部创建后台任务）"""
        self.install_sink()
        try:
            return await self.app.run_async(**kwargs)
        finally:
            self.close_input()
            self.uninstall_sink()
