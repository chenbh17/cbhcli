"""思考内容滚动显示模块（v5：滚动窗口重绘 + 破坏点防御清单）

## 显示形态（用户要求）

    💭 思考中... 12s          <- 头行（1 物理行，秒数随 chunk 实时更新）
    <最后 max_lines 行思考内容>  <- 滚动窗口：新行进来，最旧的行被顶出窗口

新内容到达 -> 窗口上移一行 -> 看到最新 8 行在滚动，旧行不占屏幕。

## 演进史与教训（v2 -> v4 -> v5）

- v2（v4.9.9）：窗口重绘（上移 N 行 + 擦除 + 重画）。数学自洽，但依赖隐式
  契约「窗口存续期间没有其他输出把光标推走、终端宽度不变」。
- v2 的重复 bug 实锤（2026-08 用户报告）：reasoning->tool_calls（无 content）
  的流中，ai_handler 在流结束后打印 `\n🔧 工具名(...)` 时窗口仍存活，光标
  被多推 2 行；finish 的 \033[{N}A 上移数错位 -> 窗口头部残留 + 新窗口画在
  下方 = 同段思考显示两遍。
- v4（v5.2.7）：append-only 全打印。零重复机制上成立，但用户否决显示形态
  （思考全文全部打印，不是有限行数滚动窗口）。
- v5（本版）：恢复 v2 滚动窗口形态 + v5.2.7 实锤修复的破坏点 + 新防御。
  rich.Live 不可用：其内部同为"上移擦除"式重绘，同病。

## 破坏点防御清单（v5 全部覆盖）

1. 🔧 工具行 print：ai_handler 已把 finish_thinking 提前到打印 🔧 之前
   （v5.2.7，见 ai_handler._stream_with_model 流结束段）；
2. 复读警告 print（思考流中）：ai_handler 打印前调用 suspend_status()
   擦除活动窗口 -> 警告行打在窗口外 -> 后续 add_content 重建窗口；
3. resize 变窄：region_width 策略（v2 保留）--窗口行宽 <= region_width，
   终端变窄时旧窗口行已被 reflow，放弃擦除留一次性快照，以新宽度重建，
   每次收窄最多 1 份快照，不会连续重复；
4. 终端过矮（高度 <= max_lines+2）：收缩窗口行数，防窗口本身超屏滚动；
5. enabled=False（exec headless / 并行子 Agent）：完全 no-op。

无后台线程：重绘由流式 chunk 驱动，头行实时显示已思考秒数。
"""
import sys
import os
import time
import unicodedata
from typing import List

from cbhcli_pkg.core.constants import C_DIM, C_RESET

# 区域换行宽度上限（越小越抗 reflow，100 是可读性与安全性的平衡）
_MAX_REGION_WIDTH = 100
# 区域换行宽度下限（极端窄终端兜底）
_MIN_REGION_WIDTH = 20


def _display_width(text: str) -> int:
    """计算字符串的终端显示宽度（中文/全角=2，其他=1）"""
    width = 0
    for ch in text:
        if ch == '\t':
            width = (width + 4) & ~3
        elif unicodedata.east_asian_width(ch) in ('W', 'F'):
            width += 2
        else:
            width += 1
    return width


def _hard_wrap_line(text: str, max_width: int) -> List[str]:
    """将一行文本按终端宽度硬换行，返回每行恰好 <= max_width 的行列表。

    这是精确擦除的核心：每个自然行 = 一个物理屏幕行，
    光标移动/窗口擦除的行数计算不再受 soft-wrap 干扰。
    """
    if max_width <= 0:
        return [text]
    result: List[str] = []
    current_line = ""
    current_width = 0

    for ch in text:
        if ch == '\t':
            ch_w = 4 - (current_width % 4)
        elif unicodedata.east_asian_width(ch) in ('W', 'F'):
            ch_w = 2
        elif ch == '\n':
            result.append(current_line)
            current_line = ""
            current_width = 0
            continue
        else:
            ch_w = 1

        if current_width + ch_w > max_width:
            result.append(current_line)
            current_line = ch
            current_width = ch_w
        else:
            current_line += ch
            current_width += ch_w

    result.append(current_line)
    return result


def _truncate_to_width(text: str, max_width: int) -> str:
    """按显示宽度截断（防头行超长破坏行数不变量）"""
    if _display_width(text) <= max_width:
        return text
    out = ""
    w = 0
    for ch in text:
        ch_w = 2 if unicodedata.east_asian_width(ch) in ('W', 'F') else 1
        if w + ch_w > max_width - 1:
            break
        out += ch
        w += ch_w
    return out + "…"


class ThinkingDisplay:
    """思考内容滚动窗口管理器（v5：窗口重绘 + 破坏点防御）

    窗口结构：
        💭 思考中... 12s        <- 头行（1 物理行，实时秒数）
        <最近 max_lines 行思考内容>  <- 每行硬换行到 region_width

    不变量：窗口每行 <= region_width <= 当前终端宽度 -> 无 soft-wrap ->
    打印 N 行后光标恰在窗口下方 N 行处，\033[{N}A + \033[J 精确擦除。
    """

    def __init__(self, max_lines: int = 8, label: str = ""):
        self.max_lines = max_lines
        self.label = label
        self.full_text = ""
        self.is_thinking = False
        # 并行子Agent等输出被捕获的场景下设为 False（禁用窗口重绘）
        self.enabled: bool = True
        self._region_lines: int = 0   # 当前窗口物理行数（0=无活动窗口）
        self._region_width: int = 0   # 窗口建立时固定的换行宽度
        self._window_lines: int = max_lines  # 当前窗口内容行数（高度自适应后可收缩）
        self._start: float = 0.0

    # ------------------------------------------------------------------
    #  基础工具
    # ------------------------------------------------------------------

    def _get_term_width(self) -> int:
        try:
            return os.get_terminal_size().columns
        except (ValueError, OSError):
            return 80

    def _get_term_height(self) -> int:
        try:
            return os.get_terminal_size().lines
        except (ValueError, OSError):
            return 24

    @staticmethod
    def _out(s: str):
        sys.stdout.write(s)
        sys.stdout.flush()

    def _new_region_width(self) -> int:
        w = self._get_term_width()
        return max(_MIN_REGION_WIDTH, min(w, _MAX_REGION_WIDTH))

    def _fit_window_lines(self) -> int:
        """终端过矮时收缩窗口行数，防窗口本身超屏滚动导致擦除错位"""
        h = self._get_term_height()
        return max(3, min(self.max_lines, h - 2))

    # ------------------------------------------------------------------
    #  渲染
    # ------------------------------------------------------------------

    def _header(self, final: bool = False) -> str:
        elapsed = int(time.monotonic() - self._start) if self._start else 0
        state = "思考完毕" if final else "思考中..."
        suffix = f" {elapsed}s" if self._start else ""
        text = f"💭 {self.label}{state}{suffix}"
        return _truncate_to_width(text, self._region_width)

    def _content_lines(self) -> List[str]:
        """窗口化内容：硬换行后取最后 _window_lines 行"""
        if not self.full_text:
            return ["..."]
        text = self.full_text.strip("\n")
        if not text:
            return ["..."]
        lines: List[str] = []
        for natural_line in text.split("\n"):
            lines.extend(_hard_wrap_line(natural_line, self._region_width))
        return lines[-self._window_lines:]

    def _erase_window(self):
        """擦除活动窗口（\033[{N}A + \033[J），置空活动状态"""
        if self._region_lines:
            self._out(f"\033[{self._region_lines}A\033[J")
            self._region_lines = 0

    def _draw(self, final: bool = False):
        """重绘窗口：先上移擦除旧窗口，再逐行打印新窗口"""
        if self._region_lines:
            self._out(f"\033[{self._region_lines}A\033[J")
        lines = [self._header(final=final)] + self._content_lines()
        buf = "".join(f"{C_DIM}{line}{C_RESET}\n" for line in lines)
        self._out(buf)
        self._region_lines = len(lines)

    # ------------------------------------------------------------------
    #  生命周期
    # ------------------------------------------------------------------

    def start_thinking(self):
        if not self.enabled or self.is_thinking:
            return
        self.is_thinking = True
        self.full_text = ""
        self._start = time.monotonic()
        self._region_width = self._new_region_width()
        self._window_lines = self._fit_window_lines()
        self._region_lines = 0
        self._draw()

    def add_content(self, content: str):
        if not self.enabled or not self.is_thinking:
            return
        self.full_text += content

        current_width = self._get_term_width()
        if current_width < self._region_width:
            # 终端变窄：已打印行可能已被 reflow，强行擦除可能误删上方内容。
            # 放弃擦除，旧窗口留作一次性快照（每次收窄最多一份），
            # 以新宽度在下方重建窗口。
            self._region_lines = 0
            self._region_width = max(_MIN_REGION_WIDTH,
                                     min(current_width, _MAX_REGION_WIDTH))
        # 高度自适应（终端变矮 -> 收缩窗口；变高 -> 恢复但不超过 max_lines）
        new_window = self._fit_window_lines()
        if new_window != self._window_lines:
            self._window_lines = new_window
        self._draw()

    def suspend_status(self):
        """擦除活动窗口（v5 语义：供外部在流式过程中打印提示行前调用）。

        外部 print 会把光标推离窗口，后续重绘的上移数将错位导致残留；
        先擦除窗口置空状态，外部行打在窗口外，后续 add_content 自动重建。
        """
        if not self.enabled:
            return
        self._erase_window()

    def finish_thinking(self):
        if not self.is_thinking:
            return
        self.is_thinking = False

        current_width = self._get_term_width()
        if current_width < self._region_width:
            # 同 add_content 的收窄处理：放弃擦除，快照留存
            self._region_lines = 0
            self._region_width = max(_MIN_REGION_WIDTH,
                                     min(current_width, _MAX_REGION_WIDTH))
        # 最终重绘（头行变为"思考完毕"）
        if self._region_lines:
            self._out(f"\033[{self._region_lines}A\033[J")
        lines = [self._header(final=True)] + self._content_lines()
        self._out("".join(f"{C_DIM}{line}{C_RESET}\n" for line in lines))
        # 窗口转为静态历史，不再管理
        self._region_lines = 0
        self._start = 0.0

    def cleanup(self):
        """强制清理终端状态（Ctrl+C 中断等异常场景）"""
        self.is_thinking = False
        if self._region_lines and self.enabled:
            try:
                self._out(f"\033[{self._region_lines}A\033[J")
            except Exception:
                pass
        self._region_lines = 0
        self._start = 0.0
        if not self.enabled:
            return  # 捕获场景（subagent/exec）不写 ANSI，防乱码
        try:
            # 确保光标可见
            sys.stdout.write("\033[?25h")
            sys.stdout.flush()
        except Exception:
            pass

    # ------------------------------------------------------------------
    #  信息查询
    # ------------------------------------------------------------------

    def get_total_content_length(self) -> int:
        return len(self.full_text)

    def get_line_count(self) -> int:
        return self.full_text.count("\n") + 1 if self.full_text else 0
