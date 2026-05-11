"""工具执行器"""
import json
import sys
import unicodedata
from typing import Optional, Callable

from cbhcli_pkg.tools.registry import ToolRegistry, ToolResult
from cbhcli_pkg.core.constants import (
    MAX_TOOL_OUTPUT_LENGTH, TOOL_PREVIEW_LENGTH,
    C_TOOL_DOT, C_TOOL_GREEN, C_TOOL_CMD, C_TOOL_RESULT,
    C_DIM, C_SEP, C_AI_HINT, C_ERROR, C_RESET
)
from cbhcli_pkg.core.errors import ToolExecutionError


# ANSI 颜色代码（用于预览显示）
C_RED_BG = "\033[41m"      # 红色背景
C_GREEN_BG = "\033[48;5;28m"  # 翡翠绿背景
C_YELLOW = "\033[33m"      # 黄色（行号）
C_BOLD = "\033[1m"         # 加粗
C_WHITE = "\033[97m"       # 亮白色
C_DIM_TEXT = "\033[90m"    # 灰色
C_SEP_LINE = "\033[36m"   # 青色分隔线


def _display_width(text: str) -> int:
    """计算字符串的显示宽度（中文占2个宽度）"""
    width = 0
    for char in text:
        if unicodedata.east_asian_width(char) in ('F', 'W'):
            width += 2
        else:
            width += 1
    return width


def _pad_to_width(text: str, target_width: int) -> str:
    """将文本填充到指定显示宽度"""
    current_width = _display_width(text)
    if current_width >= target_width:
        return text
    return text + ' ' * (target_width - current_width)


def _truncate_to_width(text: str, max_width: int) -> str:
    """截断文本到指定显示宽度"""
    width = 0
    for i, char in enumerate(text):
        char_width = 2 if unicodedata.east_asian_width(char) in ('F', 'W') else 1
        if width + char_width > max_width - 3:
            return text[:i] + "..."
        width += char_width
    return text


class ToolExecutor:
    """处理工具调用执行

    负责：
    - 工具执行前的确认
    - 工具执行
    - 结果格式化和输出
    """

    def __init__(self, tool_registry: ToolRegistry):
        """
        Args:
            tool_registry: 工具注册中心
        """
        self.tool_registry = tool_registry
        self.no_more_confirmations = False
        self.verbose = False
        self._on_tool_execute: Optional[Callable] = None

    def set_verbose(self, verbose: bool):
        """设置详细输出模式"""
        self.verbose = verbose

    def set_confirmation_mode(self, no_more_confirmations: bool):
        """设置是否跳过确认"""
        self.no_more_confirmations = no_more_confirmations

    def execute(self, tool_name: str, arguments: dict) -> ToolResult:
        """执行工具

        Args:
            tool_name: 工具名称
            arguments: 工具参数

        Returns:
            ToolResult: 执行结果
        """
        return self.tool_registry.execute(tool_name, **arguments)

    # 自带显示的工具（跳过 executor 的头部和结果显示）
    _SELF_DISPLAY_TOOLS = {"Todo"}

    def execute_with_display(
        self,
        tool_name: str,
        arguments: dict,
        tool_call_id: Optional[str] = None
    ) -> ToolResult:
        """执行工具并显示结果

        Args:
            tool_name: 工具名称
            arguments: 工具参数
            tool_call_id: 工具调用ID（用于OpenAI格式）

        Returns:
            ToolResult: 执行结果
        """
        self_display = tool_name in self._SELF_DISPLAY_TOOLS

        # 显示工具调用（自带显示的工具跳过）
        if not self_display:
            self._display_tool_call(tool_name, arguments)

        # 显示详细预览内容（确认前）
        if not self_display:
            self._display_preview(tool_name, arguments)

        # 执行前确认
        if not self._confirm_execution(tool_name):
            result = ToolResult(
                success=False,
                output="",
                error="用户取消了执行"
            )
        else:
            # 执行工具
            result = self.execute(tool_name, arguments)

        # 显示结果（自带显示的工具跳过）
        if not self_display:
            self._display_result(result)

        # 回调
        if self._on_tool_execute:
            self._on_tool_execute(tool_name, arguments, result, tool_call_id)

        return result

    def _display_tool_call(self, tool_name: str, arguments: dict):
        """显示工具调用信息"""
        cmd_preview = self._get_tool_preview(tool_name, arguments)

        print(f"\n{C_SEP}{'─' * 60}")
        if cmd_preview:
            print(f"{C_TOOL_DOT}● {C_TOOL_GREEN}{tool_name}{C_RESET}  {C_TOOL_CMD}{cmd_preview}{C_RESET}")
        else:
            print(f"{C_TOOL_DOT}● {C_TOOL_GREEN}{tool_name}{C_RESET}")

        if self.verbose:
            print(f"{C_SEP}   完整参数: {json.dumps(arguments, ensure_ascii=False)}{C_RESET}")

    def _display_preview(self, tool_name: str, arguments: dict):
        """在确认前显示详细预览内容"""
        from pathlib import Path

        if tool_name == "edit":
            file_path = arguments.get("file_path", "")
            old_str = arguments.get("old_str", "")
            new_str = arguments.get("new_str", "")

            if file_path and old_str:
                path = Path(file_path).expanduser()
                if path.exists():
                    try:
                        with open(path, 'r', encoding='utf-8') as f:
                            content = f.read()

                        # 找到 old_str 的位置
                        pos = content.find(old_str)
                        if pos != -1:
                            # 计算行号
                            prefix = content[:pos]
                            start_line = prefix.count('\n') + 1

                            old_lines = old_str.split('\n')
                            new_lines = new_str.split('\n')

                            # 移除末尾空行（避免显示为空绿行）
                            while old_lines and old_lines[-1].strip() == '':
                                old_lines.pop()
                            while new_lines and new_lines[-1].strip() == '':
                                new_lines.pop()

                            max_lines = max(len(old_lines), len(new_lines))

                            # 列宽（显示宽度）
                            content_width = 70  # 内容区域宽度
                            line_num_width = 4   # 行号宽度（与 {line_num:4d} 一致）
                            col_width = line_num_width + 1 + content_width  # 每列总宽度：行号+空格+内容

                            print()
                            # 标题行 - 左右完全分开
                            left_title = f"--- 原内容 (行 {start_line}) ---"
                            right_title = f"+++ 新内容 (行 {start_line}) +++"
                            left_padded = _pad_to_width(left_title, col_width)
                            right_padded = _pad_to_width(right_title, col_width)
                            print(f"  {C_RED_BG}{C_WHITE}{C_BOLD}{left_padded}{C_RESET}"
                                  f" {C_SEP_LINE}│{C_RESET} "
                                  f"{C_GREEN_BG}{C_WHITE}{C_BOLD}{right_padded}{C_RESET}")

                            # 分隔线
                            print(f"  {C_SEP_LINE}{'─' * col_width}┼{'─' * col_width}{C_RESET}")

                            # 左右并排显示，最多300行
                            max_display = 300
                            for i in range(min(max_lines, max_display)):
                                line_num = start_line + i

                                # 左侧（原内容）
                                if i < len(old_lines):
                                    old_line = old_lines[i]
                                    old_display = _truncate_to_width(old_line, content_width)
                                    old_padded = _pad_to_width(old_display, content_width)
                                    left = f"{C_YELLOW}{line_num:4d}{C_RESET} {C_RED_BG}{C_WHITE}{old_padded}{C_RESET}"
                                else:
                                    # 空行用空格填充，不显示颜色
                                    left = _pad_to_width('', col_width)

                                # 右侧（新内容）
                                if i < len(new_lines):
                                    new_line = new_lines[i]
                                    new_display = _truncate_to_width(new_line, content_width)
                                    new_padded = _pad_to_width(new_display, content_width)
                                    right = f"{C_YELLOW}{line_num:4d}{C_RESET} {C_GREEN_BG}{C_WHITE}{new_padded}{C_RESET}"
                                else:
                                    # 空行用空格填充，不显示颜色
                                    right = _pad_to_width('', col_width)

                                print(f"  {left} {C_SEP_LINE}│{C_RESET} {right}")

                            if max_lines > max_display:
                                print(f"  {C_DIM_TEXT}... 还有 {max_lines - max_display} 行 ...{C_RESET}")
                            print()
                    except:
                        pass

        elif tool_name == "write":
            file_path = arguments.get("file_path", "")
            content = arguments.get("content", "")

            if file_path and content:
                print()
                print(f"  {C_GREEN_BG}{C_WHITE}{C_BOLD}--- 写入内容 ---{C_RESET}")
                lines = content.split('\n')
                # 最多显示300行
                max_display = 300
                for i, line in enumerate(lines[:max_display], 1):
                    display_line = _truncate_to_width(line, 100)
                    print(f"  {C_YELLOW}{i:4d}{C_RESET} {C_GREEN_BG}{C_WHITE}{display_line}{C_RESET}")
                if len(lines) > max_display:
                    print(f"  {C_DIM_TEXT}... 还有 {len(lines) - max_display} 行 ...{C_RESET}")
                print()

        elif tool_name == "python":
            code = arguments.get("code", "")

            if code:
                print()
                print(f"  {C_GREEN_BG}{C_WHITE}{C_BOLD}--- 执行代码 ---{C_RESET}")
                lines = code.split('\n')
                # 最多显示300行
                max_display = 300
                for i, line in enumerate(lines[:max_display], 1):
                    display_line = _truncate_to_width(line, 100)
                    print(f"  {C_YELLOW}{i:4d}{C_RESET} {C_GREEN_BG}{C_WHITE}{display_line}{C_RESET}")
                if len(lines) > max_display:
                    print(f"  {C_DIM_TEXT}... 还有 {len(lines) - max_display} 行 ...{C_RESET}")
                print()

    def _get_tool_preview(self, tool_name: str, arguments: dict) -> str:
        """获取工具调用的预览字符串"""
        if tool_name == "terminal":
            cmd = arguments.get("command", "")
            if not cmd:
                cmd = arguments.get("cmd", "") or arguments.get("shell", "")
            if len(cmd) > 80 and not self.verbose:
                return cmd[:80] + "..."
            return cmd
        elif tool_name in ("read", "write", "edit"):
            path = arguments.get("path", arguments.get("file_path", ""))
            return path
        elif tool_name == "grep":
            pattern = arguments.get("pattern", "")
            path = arguments.get("path", ".")
            include = arguments.get("include", "")
            preview = f"/{pattern}/ in {path}"
            if include:
                preview += f" ({include})"
            return preview
        elif tool_name == "glob":
            return arguments.get("pattern", "")
        elif tool_name == "ask_user":
            return arguments.get("question", "")[:60]
        return ""

    def _confirm_execution(self, tool_name: str) -> bool:
        """确认是否执行工具"""
        if self.no_more_confirmations:
            return True

        # 只读/交互工具跳过确认
        if tool_name in ("grep", "glob", "ask_user", "read", "Todo",
                         "memory_search", "knowledge_base"):
            return True

        try:
            confirm = input(f"\n{C_AI_HINT}确认执行 {tool_name}? [Y/n/all]: {C_RESET}")
        except (EOFError, KeyboardInterrupt):
            return False

        confirm = confirm.strip().lower()

        if confirm == "all":
            self.no_more_confirmations = True
            return True
        elif confirm in ("n", "no"):
            return False

        return True

    def _display_result(self, result: ToolResult):
        """显示执行结果"""
        if result.success:
            output = result.output[:MAX_TOOL_OUTPUT_LENGTH] if result.output else ""

            if self.verbose:
                output_preview = output
            else:
                output_preview = output[:TOOL_PREVIEW_LENGTH]
                if len(output) > TOOL_PREVIEW_LENGTH:
                    output_preview += "..."

            print(f"{C_TOOL_RESULT}   → {output_preview}{C_RESET}")
        else:
            error_msg = result.error or "未知错误"
            print(f"{C_ERROR}   → 失败: {error_msg}{C_RESET}")

        print(f"{C_SEP}{'─' * 60}{C_RESET}")

    def on_tool_execute(self, callback: Callable):
        """设置工具执行回调

        Args:
            callback: 回调函数 (tool_name, arguments, result, tool_call_id)
        """
        self._on_tool_execute = callback
