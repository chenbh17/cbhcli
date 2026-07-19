"""工具执行器"""
import json
import shutil
import sys
from typing import Optional, Callable

from cbhcli_pkg.tools.registry import ToolRegistry, ToolResult
from cbhcli_pkg.core.constants import (
    MAX_TOOL_OUTPUT_LENGTH, TOOL_PREVIEW_LENGTH,
    C_TOOL_DOT, C_TOOL_GREEN, C_TOOL_CMD, C_TOOL_RESULT,
    C_DIM, C_SEP, C_AI_HINT, C_ERROR, C_RESET
)
from cbhcli_pkg.core.errors import ToolExecutionError
from cbhcli_pkg.core.text_width import (
    display_width as _display_width,
    pad_to_width as _pad_to_width,
    truncate_to_width as _truncate_to_width,
)


# ANSI 颜色代码（用于预览显示）
C_RED_BG = "\033[41m"      # 红色背景
C_GREEN_BG = "\033[48;5;28m"  # 翡翠绿背景
C_YELLOW = "\033[33m"      # 黄色（行号）
C_BOLD = "\033[1m"         # 加粗
C_WHITE = "\033[97m"       # 亮白色
C_DIM_TEXT = "\033[90m"    # 灰色
C_SEP_LINE = "\033[36m"   # 青色分隔线

def _term_width() -> int:
    """获取终端当前实际宽度

    直接通过 ioctl 查询（os.get_terminal_size），避免 shutil.get_terminal_size
    优先读取可能已过期的 COLUMNS 环境变量导致窗口调小后仍按旧宽度渲染。
    """
    import os
    # fileno 调用本身也可能失败（如 stdout 被重定向为 StringIO），须放进 try 内
    for fd_source in (sys.stdout, sys.stderr, None):
        try:
            fd = fd_source.fileno() if fd_source is not None else 0
            return os.get_terminal_size(fd).columns
        except (ValueError, OSError, AttributeError):
            continue
    try:
        return shutil.get_terminal_size().columns
    except Exception:
        return 80


def _separator_width() -> int:
    """工具调用分隔线宽度（适配终端，不超过60）"""
    return min(60, max(20, _term_width() - 1))


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

        print(f"\n{C_SEP}{'─' * _separator_width()}")
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

                            # rich.Table 渲染（内部按终端宽度自动选择
                            # 宽屏左右并排 / 窄屏上下堆叠，按文件类型语法高亮）
                            self._render_edit_preview(
                                old_lines, new_lines, start_line, file_path
                            )
                    except Exception:
                        pass

        elif tool_name == "write":
            file_path = arguments.get("file_path", "")
            content = arguments.get("content", "")

            if file_path and content:
                from pathlib import Path as _P
                header = f"写入内容 ({_P(file_path).name})"
                self._render_code_preview(
                    content, header, self._guess_lexer(file_path)
                )

        elif tool_name == "python":
            code = arguments.get("code", "")

            if code:
                self._render_code_preview(code, "执行代码", "python")

        elif tool_name == "image":
            image_paths = arguments.get("image_paths", [])
            prompt = arguments.get("prompt", "")

            if image_paths:
                print()
                print(f"  {C_GREEN_BG}{C_WHITE}{C_BOLD}--- 图片识别请求 ---{C_RESET}")
                print(f"  {C_DIM_TEXT}图片数量: {len(image_paths)}{C_RESET}")
                for i, path in enumerate(image_paths, 1):
                    display_path = _truncate_to_width(path, 200)
                    print(f"  {C_YELLOW}{i:4d}{C_RESET} {display_path}")
                if prompt:
                    prompt_display = _truncate_to_width(prompt, 200)
                    print(f"  {C_DIM_TEXT}识别需求: {prompt_display}{C_RESET}")
                print()

    # ==================================================================
    #  edit 预览渲染（rich.Table 表格，动态布局）
    # ==================================================================

    # 宽屏左右并排的最小终端宽度（小于此宽度改为上下堆叠）
    _EDIT_SIDE_BY_SIDE_MIN = 110
    # 最多显示的行数
    _EDIT_MAX_DISPLAY = 200

    def _render_edit_preview(self, old_lines, new_lines, start_line, file_path=""):
        """用 rich.Table 渲染编辑差异预览

        宽屏(>=110列)：左右并排表格（原内容 | 新内容）
        窄屏(<110列)：上下堆叠两个表格（先原内容，后新内容）
        长行自动折行显示（不截断），行号黄色标识。
        代码区域按文件类型做 Pygments 语法高亮（monokai 主题）。

        Args:
            old_lines: 原内容行列表
            new_lines: 新内容行列表
            start_line: 起始行号
            file_path: 目标文件路径（用于推断语法高亮 lexer）
        """
        from rich.console import Console
        from rich.table import Table
        from rich.text import Text
        from rich import box

        # 显式传入 ioctl 实时宽度（Console 默认读 COLUMNS 环境变量，可能过期）
        term_w = _term_width()
        console = Console(width=term_w)
        max_lines = max(len(old_lines), len(new_lines))
        max_display = self._EDIT_MAX_DISPLAY

        # 对完整 old/new 代码做语法高亮（保留跨行上下文），再按行切分
        lexer = self._guess_lexer(file_path) if file_path else "text"
        old_hl = self._highlight_code_lines('\n'.join(old_lines), lexer)
        new_hl = self._highlight_code_lines('\n'.join(new_lines), lexer)

        def _cell(hl_lines, i):
            """构造一个单元格：黄色行号 + 语法高亮内容"""
            if i >= len(hl_lines):
                return Text("")
            content = hl_lines[i]
            if not content.plain:
                content = Text(" ")  # 空行占位，保持行高
            return Text.assemble(
                (f"{start_line + i:>4} ", "bold yellow"),
                content,
            )

        print()
        if term_w >= self._EDIT_SIDE_BY_SIDE_MIN:
            # ---- 宽屏：左右并排 ----
            table = Table(
                box=box.SQUARE, expand=True, padding=(0, 1),
                show_lines=False, highlight=False,
            )
            table.add_column(
                f"原内容 (行 {start_line})", ratio=1, overflow="fold",
                header_style="bold white on #8b2222", style="on #2a0e0e",
            )
            table.add_column(
                f"新内容 (行 {start_line})", ratio=1, overflow="fold",
                header_style="bold white on #226622", style="on #0e2a0e",
            )
            for i in range(min(max_lines, max_display)):
                table.add_row(_cell(old_hl, i), _cell(new_hl, i))
            console.print(table)
        else:
            # ---- 窄屏：上下堆叠 ----
            old_table = Table(
                box=box.SQUARE, expand=True, padding=(0, 1),
                show_lines=False, highlight=False,
            )
            old_table.add_column(
                f"原内容 (行 {start_line})", ratio=1, overflow="fold",
                header_style="bold white on #8b2222", style="on #2a0e0e",
            )
            for i in range(min(len(old_lines), max_display)):
                old_table.add_row(_cell(old_hl, i))
            console.print(old_table)

            new_table = Table(
                box=box.SQUARE, expand=True, padding=(0, 1),
                show_lines=False, highlight=False,
            )
            new_table.add_column(
                f"新内容 (行 {start_line})", ratio=1, overflow="fold",
                header_style="bold white on #226622", style="on #0e2a0e",
            )
            for i in range(min(len(new_lines), max_display)):
                new_table.add_row(_cell(new_hl, i))
            console.print(new_table)

        if max_lines > max_display:
            console.print(f"[dim]... 还有 {max_lines - max_display} 行 ...[/dim]")
        print()

    # ==================================================================
    #  代码预览渲染（rich.Table + Syntax 语法高亮，python/write 共用）
    # ==================================================================

    # 最多显示的行数
    _CODE_MAX_DISPLAY = 300

    # 文件扩展名 → Pygments lexer 映射（write 预览用）
    _LEXER_MAP = {
        ".py": "python", ".pyw": "python",
        ".md": "markdown", ".markdown": "markdown",
        ".json": "json", ".jsonl": "json",
        ".sh": "bash", ".bash": "bash", ".zsh": "bash",
        ".yaml": "yaml", ".yml": "yaml",
        ".toml": "toml", ".ini": "ini", ".cfg": "ini",
        ".js": "javascript", ".jsx": "jsx",
        ".ts": "typescript", ".tsx": "tsx",
        ".html": "html", ".htm": "html", ".css": "css",
        ".sql": "sql", ".xml": "xml",
        ".c": "c", ".h": "c", ".cpp": "cpp", ".hpp": "cpp",
        ".java": "java", ".go": "go", ".rs": "rust", ".rb": "ruby",
    }

    def _guess_lexer(self, file_path: str) -> str:
        """根据文件扩展名推断 Pygments lexer，无法识别回退 text"""
        from pathlib import Path
        suffix = Path(file_path).suffix.lower()
        if suffix in self._LEXER_MAP:
            return self._LEXER_MAP[suffix]
        # 映射表未命中时让 pygments 猜（覆盖 Dockerfile/Makefile 等无扩展名场景）
        try:
            from pygments.lexers import get_lexer_for_filename
            from pygments.util import ClassNotFound
            lexer = get_lexer_for_filename(file_path)
            if lexer.aliases:
                return lexer.aliases[0]
        except Exception:
            pass
        return "text"

    def _highlight_code_lines(self, code: str, lexer_name: str):
        """用 Pygments 对完整代码做语法高亮，返回逐行 rich.Text 列表

        先对完整 code 做 lex（保留多行字符串/注释等跨行上下文），
        再按 \\n 把带样式的 token 流切分成行，兼容表格的逐行渲染。

        Args:
            code: 完整代码文本
            lexer_name: Pygments lexer 名（无法识别时按纯文本处理）

        Returns:
            list[rich.Text]: 每行一个 Text 对象（含 monokai 主题颜色样式）
        """
        from pygments import lex
        from pygments.lexers import get_lexer_by_name, TextLexer
        from pygments.util import ClassNotFound
        from rich.syntax import PygmentsSyntaxTheme
        from rich.text import Text

        try:
            lexer = get_lexer_by_name(lexer_name)
        except ClassNotFound:
            lexer = TextLexer()

        theme = PygmentsSyntaxTheme("monokai")
        lines = [Text()]
        for ttype, value in lex(code, lexer):
            style = theme.get_style_for_token(ttype)
            for i, part in enumerate(value.split('\n')):
                if i > 0:
                    lines.append(Text())
                if part:
                    lines[-1].append(part, style=style)
        # pygments 常在末尾补一个换行 token，切分后会多出尾部空行，
        # 需去除以保持与源行数对齐（中间空行不受影响）
        while len(lines) > 1 and not lines[-1].plain:
            lines.pop()
        return lines

    def _render_code_preview(self, code: str, header: str, lexer: str = "text"):
        """用 rich.Table + Syntax 渲染代码预览

        与 edit 预览同款表格风格（SQUARE 边框 + 绿色表头），
        代码区域使用 Pygments 语法高亮（monokai 主题，关键字/字符串/
        函数名/数字等不同颜色），自带行号，长行自动折行，宽度适配终端。

        Args:
            code: 代码内容
            header: 表头文字（如 "执行代码" / "写入内容 (xx.py)"）
            lexer: Pygments lexer 名（python/markdown/json/...，无法识别用 text）
        """
        from rich.console import Console
        from rich.table import Table
        from rich.syntax import Syntax
        from rich import box

        # 显式传入 ioctl 实时宽度（Console 默认读 COLUMNS 环境变量，可能过期）
        term_w = _term_width()
        console = Console(width=term_w)
        max_display = self._CODE_MAX_DISPLAY

        lines = code.split('\n')
        # 移除末尾空行（避免显示空代码区）
        while lines and lines[-1].strip() == '':
            lines.pop()

        display_code = '\n'.join(lines[:max_display])

        table = Table(
            box=box.SQUARE, expand=True, padding=(0, 1),
            show_lines=False, highlight=False,
        )
        table.add_column(
            header, ratio=1, overflow="fold",
            header_style="bold white on #226622",
        )
        syntax = Syntax(
            display_code, lexer,
            theme="monokai", line_numbers=True,
            word_wrap=True,
        )
        table.add_row(syntax)

        print()
        console.print(table)
        if len(lines) > max_display:
            console.print(f"[dim]... 还有 {len(lines) - max_display} 行 ...[/dim]")
        print()

    def _get_tool_preview(self, tool_name: str, arguments: dict) -> str:
        """获取工具调用的预览字符串"""
        if tool_name == "terminal":
            cmd = arguments.get("command", "")
            if not cmd:
                cmd = arguments.get("cmd", "") or arguments.get("shell", "")
            if len(cmd) > 400 and not self.verbose:
                return cmd[:400] + "..."
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
        elif tool_name == "image":
            image_paths = arguments.get("image_paths", [])
            prompt = arguments.get("prompt", "")
            count = len(image_paths)
            paths_str = ", ".join(image_paths[:3])
            if len(image_paths) > 3:
                paths_str += f" ...等{count}张"
            preview = f"[{count}张] {paths_str}"
            if prompt:
                preview += f" | {prompt[:200]}"
            return preview
        elif tool_name.startswith("cbhpacks_"):
            # cbhpacks 系列工具 - 显示所有参数
            preview_parts = []
            for key, value in arguments.items():
                # 将值转换为字符串，并截断过长的内容
                value_str = str(value)
                if len(value_str) > 50:
                    value_str = value_str[:50] + "..."
                preview_parts.append(f"{key}={value_str}")
            return ", ".join(preview_parts) if preview_parts else ""
        return ""

    def _confirm_execution(self, tool_name: str) -> bool:
        """确认是否执行工具"""
        if self.no_more_confirmations:
            return True

        # 只读/交互工具跳过确认
        if tool_name in ("grep", "glob", "ask_user", "read", "Todo",
                         "memory_search", "knowledge_base"):
            return True

        from cbhcli_pkg.core.prompt_utils import ask_text_or_none
        print()  # 与预览内容间隔一行
        confirm = ask_text_or_none(f"确认执行 {tool_name}? [Y/n/all]: ")
        if confirm is None:
            return False  # EOF / Ctrl+C 视为取消

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
            # 优先使用 display_output，否则使用 output
            display = result.display_output if result.display_output is not None else result.output
            output = display[:MAX_TOOL_OUTPUT_LENGTH] if display else ""

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

        print(f"{C_SEP}{'─' * _separator_width()}{C_RESET}")

    def on_tool_execute(self, callback: Callable):
        """设置工具执行回调

        Args:
            callback: 回调函数 (tool_name, arguments, result, tool_call_id)
        """
        self._on_tool_execute = callback
