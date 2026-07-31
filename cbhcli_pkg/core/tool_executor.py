"""工具执行器"""
import json
import shutil
import sys
import time
from typing import Optional, Callable

from cbhcli_pkg.tools.registry import ToolRegistry, ToolResult
from cbhcli_pkg.core.constants import (
    MAX_TOOL_OUTPUT_LENGTH, TOOL_PREVIEW_LENGTH,
    C_TOOL_DOT, C_TOOL_GREEN, C_TOOL_CMD, C_TOOL_RESULT,
    C_DIM, C_SEP, C_AI_HINT, C_ERROR, C_RESET
)
from cbhcli_pkg.core.errors import ToolExecutionError
from cbhcli_pkg.core.spinner import Spinner
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
        # 动画开关：并行子Agent时禁用（stdout 被 _ThreadStdoutProxy 按行
        # 捕获回放，spinner 的 \r/ANSI 序列会被打散成乱码）
        self.animations_enabled = True
        self._on_tool_execute: Optional[Callable] = None
        # Harness 组件（由 app.py 注入，均为可选）
        self.permission_engine = None   # PermissionEngine
        self.hook_manager = None        # HookManager
        self.checkpoint_manager = None  # CheckpointManager
        self.tracer = None              # Tracer
        self.session_id: str = ""       # 当前会话 ID（hooks payload 用）
        # 自定义确认回调（Web 端链条下游 Agent 用）：
        # 签名 (tool_name, arguments) -> bool，设置后替代 CLI 交互确认
        self._confirm_callback: Optional[Callable] = None
        # 自定义 ask_user 回调（Web 端链条下游 Agent 用）：
        # 签名 (question, options, allow_multiple) -> str，设置后替代 CLI 交互
        self._ask_user_callback: Optional[Callable] = None

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
        # Web 端链条下游 Agent 的 ask_user 通过回调路由到 Web UI（SSE）
        if tool_name == "ask_user" and self._ask_user_callback is not None:
            answer = self._ask_user_callback(
                arguments.get("question", ""),
                arguments.get("options", []),
                arguments.get("allow_multiple", False),
            )
            return ToolResult(success=True, output=f"用户回答: {answer}")
        return self.tool_registry.execute(tool_name, **arguments)

    # 自带显示的工具（跳过 executor 的头部和结果显示）
    _SELF_DISPLAY_TOOLS = {"Todo", "call_agent"}

    # 执行期间有自身实时输出/交互的工具（不显示 spinner 动画，避免输出打架）
    _NO_SPIN_TOOLS = {"ask_user", "process", "delegate_task", "image",
                      "skills_create", "call_agent"}

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

        permission_action = ""

        # ① PreToolUse 钩子（可拦截）
        if self.hook_manager and self.hook_manager.has_hooks("PreToolUse"):
            decision = self.hook_manager.run_pre_tool_use(
                tool_name, arguments, session_id=self.session_id
            )
            for warn in decision.warnings:
                print(f"{C_YELLOW}   ⚠️ 钩子: {warn}{C_RESET}")
            if decision.blocked:
                print(f"{C_ERROR}   ✗ 被 PreToolUse 钩子拦截: "
                      f"{decision.block_reason}{C_RESET}")
                if self.tracer:
                    self.tracer.log_tool_blocked(
                        tool_name, arguments, decision.block_reason, "hook")
                result = ToolResult(
                    success=False,
                    output="",
                    error=f"被 PreToolUse 钩子拦截: {decision.block_reason}"
                )
                self._display_result(result)
                if self._on_tool_execute:
                    self._on_tool_execute(tool_name, arguments, result, tool_call_id)
                return result

        # ② 权限规则引擎
        if self.permission_engine is not None:
            action, rule = self.permission_engine.check(tool_name, arguments)
            permission_action = action
            from cbhcli_pkg.core import permissions as _perm

            if action == _perm.DENY:
                print(f"{C_ERROR}   ✗ 被权限规则拦截: {rule}{C_RESET}")
                if self.tracer:
                    self.tracer.log_tool_blocked(
                        tool_name, arguments, str(rule), "permission")
                result = ToolResult(
                    success=False,
                    output="",
                    error=(f"操作被权限规则禁止: {rule}\n"
                           f"当前权限模式: {self.permission_engine.mode}。"
                           f"请改用其他方式完成任务，或请用户切换权限模式/调整规则。")
                )
                self._display_result(result)
                if self._on_tool_execute:
                    self._on_tool_execute(tool_name, arguments, result, tool_call_id)
                return result

            if action == _perm.WARN:
                print(f"{C_ERROR}   ⚠️ [YOLO] 命中红线规则 {rule}，已放行{C_RESET}")

            confirmed = True
            if action == _perm.ASK:
                confirmed = self._confirm_execution(tool_name, arguments)
            # ALLOW / WARN → 跳过人工确认
        else:
            # 无权限引擎（兼容路径）：走原有人工确认
            confirmed = self._confirm_execution(tool_name, arguments)

        # ③ 人工确认未通过
        if not confirmed:
            if self.tracer:
                self.tracer.log_tool_blocked(
                    tool_name, arguments, "用户取消了执行", "user")
            result = ToolResult(
                success=False,
                output="",
                error="用户取消了执行"
            )
        else:
            # ④ 写操作前备份检查点
            if (self.checkpoint_manager and
                    tool_name in ("write", "edit")):
                file_path = arguments.get("file_path", "")
                if file_path:
                    self.checkpoint_manager.backup(file_path, tool_name)

            # ⑤ 执行工具（带 spinner 动画 + 计时）
            start = time.monotonic()
            use_spinner = (self.animations_enabled
                           and not self_display
                           and tool_name not in self._NO_SPIN_TOOLS)
            if use_spinner:
                with Spinner(f"执行中 {tool_name}", color=C_TOOL_GREEN,
                             dim=C_DIM, reset=C_RESET):
                    result = self.execute(tool_name, arguments)
            else:
                result = self.execute(tool_name, arguments)
            result.duration_ms = int((time.monotonic() - start) * 1000)

            # ⑥ PostToolUse 钩子（反馈追加给模型）
            if self.hook_manager and self.hook_manager.has_hooks("PostToolUse"):
                decision = self.hook_manager.run_post_tool_use(
                    tool_name, arguments,
                    result.output or result.error or "",
                    session_id=self.session_id
                )
                for warn in decision.warnings:
                    print(f"{C_YELLOW}   ⚠️ 钩子: {warn}{C_RESET}")
                feedback = decision.merged_output()
                if feedback:
                    result.output = (result.output or "") + \
                        f"\n\n[PostToolUse 钩子反馈]\n{feedback}"

        # 显示结果（自带显示的工具跳过）
        if not self_display:
            self._display_result(result)

        # 可观测性 trace
        if self.tracer:
            self.tracer.log_tool_call(
                tool_name, arguments,
                permission=permission_action,
                duration_ms=getattr(result, "duration_ms", 0),
                success=result.success,
                error=result.error or ""
            )

        # 回调
        if self._on_tool_execute:
            self._on_tool_execute(tool_name, arguments, result, tool_call_id)

        return result

    def _display_tool_call(self, tool_name: str, arguments: dict):
        """显示工具调用信息

        状态指示：○ 待执行（暗） → ⠋ 执行中（braille动画，见 Spinner）
        → ● 完成（绿/红，见 _display_result）。
        注：不使用 ⏺（U+23FA），部分终端字体无此字形会显示为豆腐方块。
        """
        cmd_preview = self._get_tool_preview(tool_name, arguments)

        print(f"\n{C_SEP}{'─' * _separator_width()}")
        if cmd_preview:
            print(f"{C_TOOL_GREEN}○ {C_BOLD}{tool_name}{C_RESET}"
                  f"  {C_TOOL_CMD}{cmd_preview}{C_RESET}")
        else:
            print(f"{C_TOOL_GREEN}○ {C_BOLD}{tool_name}{C_RESET}")

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

                        # 统一查找（精确/空白宽容/Unicode转义宽容），
                        # 宽容匹配时 old_str 可能与文件实际内容不同，
                        # 用文件实际匹配的文本渲染预览
                        from cbhcli_pkg.tools.file_edit import find_edit_matches
                        spans, matched_text = find_edit_matches(content, old_str)
                        if spans:
                            pos = spans[0][0]
                            # 计算行号
                            prefix = content[:pos]
                            start_line = prefix.count('\n') + 1

                            old_lines = matched_text.split('\n')
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

    def _edit_inline_marks(self, old_lines, new_lines):
        """计算行内字符级差异区间

        公共前缀/后缀行不标记；中间变更区行数一致时逐行对比，
        仅差异字符段返回标记；行数不一致时变更行整行标记。

        Returns:
            (old_marks, new_marks): 每行的 [(start, end), ...] 字符区间列表
        """
        n_old, n_new = len(old_lines), len(new_lines)
        old_marks = [[] for _ in old_lines]
        new_marks = [[] for _ in new_lines]

        # 公共前缀行 / 后缀行（完全相同，不标记）
        pre = 0
        max_pre = min(n_old, n_new)
        while pre < max_pre and old_lines[pre] == new_lines[pre]:
            pre += 1
        suf = 0
        while suf < max_pre - pre and \
                old_lines[n_old - 1 - suf] == new_lines[n_new - 1 - suf]:
            suf += 1

        old_mid = old_lines[pre:n_old - suf]
        new_mid = new_lines[pre:n_new - suf]

        if len(old_mid) == len(new_mid):
            # 行数一致：逐行公共前后缀字符对比，仅中段差异标记
            for k, (a, b) in enumerate(zip(old_mid, new_mid)):
                if a == b:
                    continue
                p = 0
                m = min(len(a), len(b))
                while p < m and a[p] == b[p]:
                    p += 1
                sa, sb = len(a), len(b)
                while sa > p and sb > p and a[sa - 1] == b[sb - 1]:
                    sa -= 1
                    sb -= 1
                if sa > p:
                    old_marks[pre + k].append((p, sa))
                if sb > p:
                    new_marks[pre + k].append((p, sb))
        else:
            # 行数不一致：变更行整行标记（至少标1字符保证空行可见）
            for k in range(len(old_mid)):
                old_marks[pre + k].append((0, max(1, len(old_mid[k]))))
            for k in range(len(new_mid)):
                new_marks[pre + k].append((0, max(1, len(new_mid[k]))))

        return old_marks, new_marks

    def _render_edit_preview(self, old_lines, new_lines, start_line, file_path=""):
        """用 rich.Table 渲染编辑差异预览

        宽屏(>=110列)：左右并排表格（原内容 | 新内容）
        窄屏(<110列)：上下堆叠两个表格（先原内容，后新内容）
        长行自动折行显示（不截断），行号黄色标识。
        代码区域按文件类型做 Pygments 语法高亮（monokai 主题）。
        行内字符级对比：仅差异字符段加亮底色，未变更部分保持列底色。

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

        # 差异段高亮样式（比列底色更亮一档，且文字加粗）
        DEL_SEG_STYLE = "bold on #7a2626"
        ADD_SEG_STYLE = "bold on #267a26"

        # 显式传入 ioctl 实时宽度（Console 默认读 COLUMNS 环境变量，可能过期）
        term_w = _term_width()
        console = Console(width=term_w)
        max_lines = max(len(old_lines), len(new_lines))
        max_display = self._EDIT_MAX_DISPLAY

        # 对完整 old/new 代码做语法高亮（保留跨行上下文），再按行切分
        lexer = self._guess_lexer(file_path) if file_path else "text"
        old_hl = self._highlight_code_lines('\n'.join(old_lines), lexer)
        new_hl = self._highlight_code_lines('\n'.join(new_lines), lexer)

        # 行内字符级差异区间
        old_marks, new_marks = self._edit_inline_marks(old_lines, new_lines)

        def _cell(hl_lines, i, marks, seg_style):
            """构造一个单元格：黄色行号 + 语法高亮内容 + 差异段底色"""
            if i >= len(hl_lines):
                return Text("")
            content = hl_lines[i].copy()
            if not content.plain:
                content = Text(" ")  # 空行占位，保持行高
            for (s, e) in marks:
                content.stylize(seg_style, s, e)
            return Text.assemble(
                (f"{start_line + i:>4} ", "bold yellow"),
                content,
            )

        def _marks(marks, i):
            """宽屏并排时新旧行数可能不一致，越界返回空标记"""
            return marks[i] if i < len(marks) else []

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
                table.add_row(
                    _cell(old_hl, i, _marks(old_marks, i), DEL_SEG_STYLE),
                    _cell(new_hl, i, _marks(new_marks, i), ADD_SEG_STYLE),
                )
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
                old_table.add_row(_cell(old_hl, i, _marks(old_marks, i), DEL_SEG_STYLE))
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
                new_table.add_row(_cell(new_hl, i, _marks(new_marks, i), ADD_SEG_STYLE))
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

    def _confirm_execution(self, tool_name: str,
                           arguments: Optional[dict] = None) -> bool:
        """确认是否执行工具

        [Y/n/all/always]:
          Y      本次放行
          n      本次拒绝
          all    本会话内不再确认（deny 红线除外）
          always 本次放行，并提炼一条 allow 规则永久生效（写入 permissions.json）
        """
        # 只读/交互工具跳过确认
        if tool_name in ("grep", "glob", "ask_user", "read", "Todo",
                         "memory_search", "knowledge_base", "call_agent"):
            return True

        # 自定义确认回调优先（Web 端链条下游 Agent 确认走 SSE 流程，
        # 回调内部自行处理 all 逻辑，因此先于 no_more_confirmations 检查）
        if self._confirm_callback is not None:
            return self._confirm_callback(tool_name, arguments)

        if self.no_more_confirmations:
            return True

        from cbhcli_pkg.core.prompt_utils import ask_text_or_none
        print()  # 与预览内容间隔一行
        confirm = ask_text_or_none(
            f"确认执行 {tool_name}? [Y/n/all/always]: ")
        if confirm is None:
            return False  # EOF / Ctrl+C 视为取消

        confirm = confirm.strip().lower()

        if confirm == "all":
            self.no_more_confirmations = True
            return True
        elif confirm == "always":
            if self.permission_engine is not None and arguments is not None:
                from cbhcli_pkg.core.permissions import PermissionEngine
                rule = PermissionEngine.suggest_allow_rule(tool_name, arguments)
                self.permission_engine.add_rule("allow", rule)
                print(f"{C_TOOL_GREEN}   ✓ 已添加永久放行规则: {rule}{C_RESET}")
            return True
        elif confirm in ("n", "no"):
            return False

        return True

    def _display_result(self, result: ToolResult):
        """显示执行结果（✓/✗ 状态行 + 耗时 + ⎿ 树形结果）"""
        duration = getattr(result, "duration_ms", 0)
        duration_str = f" {C_DIM}· {duration / 1000:.1f}s{C_RESET}" \
            if duration >= 100 else ""

        if result.success:
            print(f"{C_TOOL_GREEN}  ● 完成{C_RESET}{duration_str}")
            # 优先使用 display_output，否则使用 output
            display = result.display_output if result.display_output is not None else result.output
            output = display[:MAX_TOOL_OUTPUT_LENGTH] if display else ""

            if self.verbose:
                output_preview = output
            else:
                output_preview = output[:TOOL_PREVIEW_LENGTH]
                if len(output) > TOOL_PREVIEW_LENGTH:
                    output_preview += "..."

            if output_preview:
                # ⎿ 树形引导线 + 后续行缩进对齐
                lines = output_preview.split('\n')
                max_lines = len(lines) if self.verbose else 12
                shown = lines[:max_lines]
                for i, line in enumerate(shown):
                    prefix = "  ⎿  " if i == 0 else "     "
                    print(f"{C_TOOL_RESULT}{prefix}{line}{C_RESET}")
                if len(lines) > max_lines:
                    print(f"{C_DIM}     … 共 {len(lines)} 行"
                          f"（Ctrl+R 详细模式查看全部）{C_RESET}")
        else:
            error_msg = result.error or "未知错误"
            print(f"{C_ERROR}  ● 失败{C_RESET}{duration_str}")
            err_lines = error_msg.split('\n')
            for i, line in enumerate(err_lines[:8]):
                prefix = "  ⎿  " if i == 0 else "     "
                print(f"{C_ERROR}{prefix}{line}{C_RESET}")
            if len(err_lines) > 8:
                print(f"{C_DIM}     … 共 {len(err_lines)} 行{C_RESET}")

        print(f"{C_SEP}{'─' * _separator_width()}{C_RESET}")

    def on_tool_execute(self, callback: Callable):
        """设置工具执行回调

        Args:
            callback: 回调函数 (tool_name, arguments, result, tool_call_id)
        """
        self._on_tool_execute = callback
