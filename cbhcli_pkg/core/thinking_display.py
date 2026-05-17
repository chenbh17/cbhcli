"""思考内容滚动显示模块"""
import sys
import os
from typing import Optional

from rich.console import Console
from rich.live import Live
from rich.text import Text

from cbhcli_pkg.core.constants import C_DIM, C_RESET


class ThinkingDisplay:
    """思考内容滚动显示管理器

    使用 rich.Live 实现原地刷新，transient=False 保留最终内容。
    不用 Panel（无框），与正常输出等宽。
    不手动换行，由 rich Text 自然处理。
    """

    def __init__(self, max_lines: int = 8, label: str = ""):
        self.console = Console()
        self.max_lines = max_lines
        self.label = label
        self.full_text = ""
        self.live: Optional[Live] = None
        self.is_thinking = False

    def start_thinking(self):
        if self.is_thinking:
            return
        self.is_thinking = True
        self.full_text = ""
        self.live = Live(
            self._render(),
            console=self.console,
            refresh_per_second=15,
            transient=False,
        )
        self.live.start()

    def add_content(self, content: str):
        if not self.is_thinking or not self.live:
            return
        self.full_text += content
        self.live.update(self._render())

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
        """去除首尾空行，按自然行截断，只保留最后 max_lines 行"""
        text = text.strip("\n")
        if not text:
            return ""
        lines = text.split("\n")
        if len(lines) > self.max_lines:
            lines = lines[-self.max_lines:]
        return "\n".join(lines)

    def _render(self) -> Text:
        text = Text()
        text.append(f"💭 {self.label}思考中...\n", style="dim")
        if not self.full_text:
            text.append("  ...", style="dim")
        else:
            display = self._truncate_text(self.full_text)
            text.append(f"  {display}", style="dim")
        return text

    def _render_finished(self) -> Text:
        text = Text()
        text.append(f"💭 {self.label}思考完毕\n", style="dim")
        if not self.full_text:
            text.append("  （无内容）", style="dim")
        else:
            display = self._truncate_text(self.full_text)
            text.append(f"  {display}", style="dim")
        return text

    def get_total_content_length(self) -> int:
        return len(self.full_text)

    def get_line_count(self) -> int:
        return self.full_text.count("\n") + 1 if self.full_text else 0
