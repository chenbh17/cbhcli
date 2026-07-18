"""思考内容滚动显示模块

resize 免疫方案:
- auto_refresh=False: 禁用后台线程, 避免与手动 update 竞态导致重复渲染
- resize 时重新创建 Console(刷新缓存宽度) + 彻底重启 Live
- transient=True 在重启时先 stop 旧 Live 清除旧内容, 再 start 新 Live

soft-wrap 修复:
- _truncate_text 按屏幕行数（而非自然行数）截断，避免长行 soft wrap
  后实际显示行数超出 max_lines，导致 Live 清除行数不足产生重叠
"""
import sys
import os
import unicodedata
from typing import Optional, List

from rich.console import Console
from rich.live import Live
from rich.text import Text

from cbhcli_pkg.core.constants import C_DIM, C_RESET


def _display_width(text: str) -> int:
    """计算字符串的终端显示宽度（中文/全角=2，其他=1）"""
    width = 0
    for ch in text:
        if ch == '\t':
            width = (width + 4) & ~3  # tab 对齐到4的倍数
        elif unicodedata.east_asian_width(ch) in ('W', 'F'):
            width += 2
        else:
            width += 1
    return width


def _hard_wrap_line(text: str, max_width: int) -> List[str]:
    """将一行文本按终端宽度硬换行（手动插入换行），返回拆分为恰好一屏宽的行列表。
    
    这是解决重叠问题的核心：Rich 的 Live 组件通过自然行数来计算清除行数，
    但长行会因为 soft-wrap 导致实际屏幕行数 > 自然行数，清除不足产生重叠。
    先 hard-wrap 后，每个自然行 = 一个屏幕行，Rich 的计算就完全精确。
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
            # 原始换行符：结束当前行，开始新行
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


class ThinkingDisplay:
    """思考内容滚动显示管理器

    使用 rich.Live 实现原地刷新，transient=False 保留最终内容。
    通过检测终端宽度变化并自动重启 Live 来免疫 resize 导致的
    行数偏移问题（窗口变窄后旧内容换行增多，光标上移行数不足）。
    不用 Panel（无框），与正常输出等宽。
    """

    def __init__(self, max_lines: int = 8, label: str = ""):
        self.console = Console()
        self.max_lines = max_lines
        self.label = label
        self.full_text = ""
        self.live: Optional[Live] = None
        self.is_thinking = False
        self._term_width: int = 0
        # 并行子Agent等输出被捕获的场景下设为 False：
        # 禁用 rich.Live 原地刷新（其光标控制序列在回放时会产生乱码）
        self.enabled: bool = True

    def _get_term_width(self) -> int:
        try:
            return os.get_terminal_size().columns
        except (ValueError, OSError):
            return 80

    def _live_start(self):
        """创建并启动 Live，同时记录当前终端宽度。

        使用 auto_refresh=False 禁用后台刷新线程，避免与手动 update 竞态
        导致 resize 时渲染重叠。
        """
        self._term_width = self._get_term_width()
        self.live = Live(
            self._render(),
            console=self.console,
            refresh_per_second=20,
            transient=False,
            auto_refresh=False,
        )
        self.live.start()

    def _live_restart_if_resized(self) -> bool:
        """检测终端宽度是否变化，若变化则彻底重启 Live。返回 True 表示已重启。"""
        new_width = self._get_term_width()
        if new_width != self._term_width:
            # Step 1: 先停止旧 Live 并清除其输出
            if self.live:
                # 临时设置 transient=True，stop 时会调用 restore_cursor 清除内容
                self.live.transient = True
                self.live.stop()
                self.live = None

            # Step 2: 重新创建 Console（刷新缓存的终端宽度）
            self.console = Console()

            # Step 3: 启动新 Live（新 Console + 新宽度）
            self._live_start()
            return True
        return False

    def start_thinking(self):
        if not self.enabled or self.is_thinking:
            return
        self.is_thinking = True
        self.full_text = ""
        self._live_start()

    def add_content(self, content: str):
        if not self.enabled or not self.is_thinking or not self.live:
            return
        self.full_text += content
        # 先检测 resize，若已重启则跳过 update（_live_start 已渲染新内容）
        if self._live_restart_if_resized():
            return
        # auto_refresh=False，需要手动 refresh 让内容实时显示
        self.live.update(self._render(), refresh=True)

    def finish_thinking(self):
        if not self.is_thinking:
            return
        self.is_thinking = False
        if self.live:
            self.live.update(self._render_finished())
            self.live.stop()
            self.live = None

    def cleanup(self):
        """强制清理终端状态（用于 Ctrl+C 中断等异常场景）"""
        self.is_thinking = False
        if self.live:
            try:
                self.live.stop()
            except Exception:
                pass
            self.live = None
        # 强制刷新终端，确保光标可见
        sys.stdout.write("\033[?25h")  # 显示光标
        sys.stdout.flush()

    def _truncate_text(self, text: str) -> str:
        """去除首尾空行，按屏幕行数（hard-wrap 后）截断，只保留最后 max_lines 行。
        
        核心修复：先将文本按终端宽度 hard-wrap（手动插入换行），使每个"自然行"
        恰好等于一个"屏幕行"。然后再取最后 max_lines 行。这样 Rich 的 Live 组件
        统计到的自然行数 = 屏幕行数，清除行数完全精确，不会产生重叠。
        """
        text = text.strip("\n")
        if not text:
            return ""

        term_width = max(10, self._get_term_width())

        # Step 1: 先 hard-wrap 所有内容，确保每行宽度 ≤ term_width
        all_lines: List[str] = []
        for natural_line in text.split("\n"):
            wrapped = _hard_wrap_line(natural_line, term_width)
            all_lines.extend(wrapped)

        # Step 2: 只保留最后 max_lines 行
        if len(all_lines) > self.max_lines:
            all_lines = all_lines[-self.max_lines:]

        return "\n".join(all_lines)

    def _render(self) -> Text:
        text = Text()
        text.append(f"💭 {self.label}思考中...\n", style="dim")
        if not self.full_text:
            text.append("...", style="dim")
        else:
            display = self._truncate_text(self.full_text)
            text.append(f"{display}", style="dim")
        return text

    def _render_finished(self) -> Text:
        text = Text()
        text.append(f"💭 {self.label}思考完毕\n", style="dim")
        if not self.full_text:
            text.append("（无内容）", style="dim")
        else:
            display = self._truncate_text(self.full_text)
            text.append(f"{display}", style="dim")
        return text

    def get_total_content_length(self) -> int:
        return len(self.full_text)

    def get_line_count(self) -> int:
        return self.full_text.count("\n") + 1 if self.full_text else 0
