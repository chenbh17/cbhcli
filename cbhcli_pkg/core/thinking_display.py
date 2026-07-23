"""思考内容滚动显示模块（手动 ANSI 区域重绘，resize 安全）

为什么放弃 rich.Live（v4.9.9 重写）：
    Live 按"自然行数"上移光标擦除。终端变窄后，已打印的长行被终端
    reflow（1 行软换成 N 行），物理行数 > 自然行数，擦除不足 →
    每来一个 chunk 就残留一行 💭 思考中...（连续重复）。

本方案的精确性来自三点：
1. 区域每行都硬换行到 region_width（≤ 终端宽度），自然行数=物理行数，
   光标上移 \\033[{n}A + 擦除到屏幕尾 \\033[J 的数学完全精确；
2. region_width 在区域建立时固定为 min(终端宽度, 100)。终端宽度不变
   或变宽时，已打印行绝不会被 reflow（长度 ≤ region_width ≤ 当前宽度），
   原地重绘永远安全；
3. 仅当终端变得比 region_width 更窄（内容可能已被 reflow，强行擦除
   可能误删上方 AI 回答），放弃擦除：旧区域作为一次性快照留存，光标
   已在区域下方，直接以新宽度重建区域。每次收窄最多 1 个快照，
   不会出现连续重复。

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
    """将一行文本按终端宽度硬换行，返回每行恰好 ≤ max_width 的行列表。

    这是精确擦除的核心：每个自然行 = 一个物理屏幕行，
    光标移动/区域擦除的行数计算不再受 soft-wrap 干扰。
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
    """思考内容滚动显示管理器（手动 ANSI 区域重绘）

    区域结构：
        💭 思考中... 12s        ← 头行（1 物理行，实时秒数）
        <最近 max_lines 行思考内容>  ← 每行硬换行到 region_width

    不变量：区域每行 ≤ region_width ≤ 当前终端宽度 → 无 soft-wrap →
    打印 N 行后光标恰在区域下方 N 行处，\\033[{N}A + \\033[J 精确擦除。
    """

    def __init__(self, max_lines: int = 8, label: str = ""):
        self.max_lines = max_lines
        self.label = label
        self.full_text = ""
        self.is_thinking = False
        # 并行子Agent等输出被捕获的场景下设为 False（禁用原地重绘）
        self.enabled: bool = True
        self._region_lines: int = 0   # 当前区域物理行数（0=无活动区域）
        self._region_width: int = 0   # 区域建立时固定的换行宽度
        self._start: float = 0.0

    # ------------------------------------------------------------------
    #  基础工具
    # ------------------------------------------------------------------

    def _get_term_width(self) -> int:
        try:
            return os.get_terminal_size().columns
        except (ValueError, OSError):
            return 80

    @staticmethod
    def _out(s: str):
        sys.stdout.write(s)
        sys.stdout.flush()

    def _new_region_width(self) -> int:
        w = self._get_term_width()
        return max(_MIN_REGION_WIDTH, min(w, _MAX_REGION_WIDTH))

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
        """窗口化内容：硬换行后取最后 max_lines 行"""
        if not self.full_text:
            return ["..."]
        text = self.full_text.strip("\n")
        if not text:
            return ["..."]
        lines: List[str] = []
        for natural_line in text.split("\n"):
            lines.extend(_hard_wrap_line(natural_line, self._region_width))
        return lines[-self.max_lines:]

    def _draw(self, final: bool = False):
        """重绘区域：先上移擦除旧区域，再逐行打印新区域"""
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
        self._region_lines = 0
        self._draw()

    def add_content(self, content: str):
        if not self.enabled or not self.is_thinking:
            return
        self.full_text += content

        current_width = self._get_term_width()
        if current_width < self._region_width:
            # 终端窄于区域宽度：已打印行可能已被 reflow，强行擦除可能
            # 误删上方内容。放弃擦除，旧区域留作快照（每次收窄最多一次），
            # 以新宽度在下方重建区域。
            self._region_lines = 0
            self._region_width = max(_MIN_REGION_WIDTH,
                                     min(current_width, _MAX_REGION_WIDTH))
        self._draw()

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
        # 区域转为静态历史，不再管理
        self._region_lines = 0
        self._start = 0.0

    def cleanup(self):
        """强制清理终端状态（Ctrl+C 中断等异常场景）"""
        self.is_thinking = False
        if self._region_lines:
            try:
                self._out(f"\033[{self._region_lines}A\033[J")
            except Exception:
                pass
            self._region_lines = 0
        self._start = 0.0
        # 确保光标可见
        sys.stdout.write("\033[?25h")
        sys.stdout.flush()

    # ------------------------------------------------------------------
    #  信息查询
    # ------------------------------------------------------------------

    def get_total_content_length(self) -> int:
        return len(self.full_text)

    def get_line_count(self) -> int:
        return self.full_text.count("\n") + 1 if self.full_text else 0
