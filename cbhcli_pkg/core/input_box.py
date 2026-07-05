"""聊天输入框组件 — 基于 prompt_toolkit 原生补全系统

设计目标：
1. 支持中英文、emoji 输入，退格不错位（由 prompt_toolkit 原生处理）
2. 支持换行（Alt+Enter / Ctrl+J）
3. 终端窗口变动不影响输入框（无边框，prompt_toolkit 自动适配）
4. 初始只显示一行（❯ 提示符 + 光标）
5. 斜杠命令补全（原生 CompletionMenu + Column 渲染）
"""
from __future__ import annotations

from prompt_toolkit import PromptSession
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.document import Document
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.styles import Style
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.filters import Condition
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from cbhcli_pkg.commands.parser import SlashCommandParser


class SlashCommandCompleter(Completer):
    """斜杠命令补全器 — 适配 prompt_toolkit 原生补全系统
    
    利用 SlashCommandHelper 的 compute() 方法获取补全列表，
    转换为 prompt_toolkit Completion 对象。
    """
    
    def __init__(self, cmd_helper):
        """初始化
        
        Args:
            cmd_helper: SlashCommandHelper 实例
        """
        self._helper = cmd_helper
    
    def get_completions(self, document: Document, complete_event):
        """获取补全列表（prompt_toolkit 调用）
        
        prompt_toolkit 会在用户输入时自动调用此方法。
        """
        text = document.text_before_cursor
        
        # 只对斜杠命令进行补全
        if not text.startswith('/'):
            return
        
        # 获取补全列表 [(display, desc, full_cmd), ...]
        completions = self._helper.compute(text)
        
        # 计算需要替换的文本范围
        # prompt_toolkit 的 Completion 需要指定 start_position（相对于光标的负偏移）
        # 我们替换整个当前输入文本
        start_pos = -len(text)
        
        for display, desc, full_cmd in completions:
            yield Completion(
                full_cmd,
                start_position=start_pos,
                display=display,
                display_meta=desc,
            )


class ChatInputBox:
    """聊天输入框 — 无边框设计，基于 prompt_toolkit 原生补全
    
    特性：
    - ❯ 提示符，单行起始，自动扩展
    - prompt_toolkit 原生补全菜单（MultiColumn / Column 样式）
    - Alt+Enter / Ctrl+J 换行
    - bottom_toolbar 显示状态栏（模型 | 上下文 | 路径）
    - 终端 resize 自动适配，无重叠
    - 中英文 / emoji 退格不错位
    """
    
    def __init__(self, cmd_helper, app):
        """初始化聊天输入框
        
        Args:
            cmd_helper: SlashCommandHelper 实例
            app: CBHCLIApp 实例（用于获取状态信息）
        """
        self._helper = cmd_helper
        self._app = app
        
        # 补全器
        self._completer = SlashCommandCompleter(cmd_helper)
        
        # 样式
        self._style = Style.from_dict({
            'prompt': 'bold #00bcd4',
            'bottom-toolbar': 'noinherit #555555',
            'bottom-toolbar.model': 'bold #00bcd4',
            'bottom-toolbar.ctx': '#888888',
            'bottom-toolbar.cwd': '#666666',
            'bottom-toolbar.sep': '#444444',
            'completion-menu': 'bg:#333333',
            'completion-menu.completion': 'bg:#333333 #cccccc',
            'completion-menu.completion.current': 'bold bg:#00bcd4 #000000',
            'completion-menu.meta.completion': 'bg:#333333 #888888',
            'completion-menu.meta.completion.current': 'bg:#00bcd4 #444444',
        })
        
        # 快捷键
        self._bindings = KeyBindings()
        self._setup_keybindings()
        
        # PromptSession
        self._session = PromptSession(
            style=self._style,
            key_bindings=self._bindings,
            completer=self._completer,
            complete_style='multi_column',  # 多列补全菜单
            multiline=True,
            complete_while_typing=True,
        )
    
    def _setup_keybindings(self):
        """设置快捷键"""
        
        @self._bindings.add('enter')
        def _submit(event):
            """Enter 提交（multiline 模式下需显式绑定）"""
            event.current_buffer.validate_and_handle()
        
        @self._bindings.add('c-j')
        def _newline(event):
            """Ctrl+J 换行"""
            event.current_buffer.insert_text('\n')
        
        @self._bindings.add('escape', 'enter')
        def _newline_alt(event):
            """Alt+Enter 换行"""
            event.current_buffer.insert_text('\n')
        
        @self._bindings.add('c-r')
        def _toggle_verbose(event):
            """Ctrl+R 切换工具显示详细/简洁模式"""
            self._app.tool_verbose = not self._app.tool_verbose
            self._app.tool_executor.set_verbose(self._app.tool_verbose)
            from cbhcli_pkg.core.constants import C_SEP, C_RESET, C_DIM
            mode = "详细" if self._app.tool_verbose else "简洁"
            print(f"\n{C_SEP}工具显示: {mode}{C_RESET}")
    
    def _build_toolbar(self):
        """构建底部状态栏（单行）"""
        app = self._app
        
        # 模型名
        model_name = "未配置模型"
        if app.llm_client and hasattr(app.llm_client, 'model_name'):
            model_name = app.llm_client.model_name
        elif app.current_agent_config and app.current_agent_config.primary_model:
            model_name = app.current_agent_config.primary_model
        
        # 上下文信息
        ctx_info = ""
        if app.session and app.context_window:
            from cbhcli_pkg.context.token_counter import get_token_counter
            total_tokens = app.session.get_total_tokens(app.token_counter)
            app.context_window.update(total_tokens)
            pct = app.context_window.usage_percentage() * 100
            limit = app.context_window.model_limit
            ctx_info = f"ctx: {total_tokens:,}/{limit:,} ({pct:.1f}%)"
        
        # 当前路径
        import os
        cwd = os.getcwd()
        # 截断过长的路径
        if len(cwd) > 40:
            cwd = "..." + cwd[-37:]
        
        # Agent名
        agent = app.current_agent_name or ""
        
        # 技能
        skills_str = ""
        if app.skill_manager:
            active_names = app.skill_manager.get_active_skill_names()
            if active_names:
                skills_str = ', '.join(active_names)
        
        # 构建 HTML 格式化文本
        parts = []
        parts.append(f'<style class="bottom-toolbar.model">{model_name}</style>')
        if ctx_info:
            parts.append(f'<style class="bottom-toolbar.sep"> | </style>')
            parts.append(f'<style class="bottom-toolbar.ctx">{ctx_info}</style>')
        if agent:
            parts.append(f'<style class="bottom-toolbar.sep"> | </style>')
            parts.append(f'<style class="bottom-toolbar.cwd">{agent}</style>')
        if skills_str:
            parts.append(f'<style class="bottom-toolbar.sep"> | </style>')
            parts.append(f'<style class="bottom-toolbar.ctx">skills: {skills_str}</style>')
        parts.append(f'<style class="bottom-toolbar.sep"> | </style>')
        parts.append(f'<style class="bottom-toolbar.cwd">{cwd}</style>')
        
        return HTML(''.join(parts))
    
    def prompt(self) -> str:
        """显示提示符并获取用户输入
        
        Returns:
            用户输入文本（已 strip），EOFError 时返回 "quit"，KeyboardInterrupt 时返回 ""
        """
        # 提示符：❯ 加 Agent 名
        agent = self._app.current_agent_name or ""
        prompt_text = HTML(f'<style class="prompt">❯ </style>')
        
        try:
            user_input = self._session.prompt(
                prompt_text,
                prompt_continuation=HTML('<style class="bottom-toolbar">  </style>'),
                bottom_toolbar=self._build_toolbar,
            )
            return user_input.strip()
        except EOFError:
            return "quit"
        except KeyboardInterrupt:
            return ""
    
    def print_user_echo(self, user_input: str):
        """打印用户输入回显（提交后高亮显示）
        
        不再清除输入框行，而是直接在输入下方打印高亮版本。
        prompt_toolkit 提交后会自动清除输入区域和 toolbar，
        所以直接 print 即可。
        """
        from cbhcli_pkg.core.constants import C_USER_BG, C_USER_FG, C_RESET
        
        agent = self._app.current_agent_name or ""
        prefix = f"[{agent}] " if agent else ""
        
        lines = user_input.split('\n')
        first_line = lines[0]
        print(f"{C_USER_BG}{C_USER_FG}▌ {prefix}> {first_line}{C_RESET}")
        for line in lines[1:]:
            print(f"{C_USER_BG}{C_USER_FG}  {' ' * len(prefix)}  {line}{C_RESET}")