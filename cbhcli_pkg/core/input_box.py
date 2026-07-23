"""聊天输入框组件 — 基于 prompt_toolkit 原生补全系统

设计目标：
1. 支持中英文、emoji 输入，退格不错位（字素簇宽度补丁修复 ZWJ/VS16/旗帜/肤色 emoji）
2. 支持换行（Alt+Enter / Ctrl+J）
3. 终端窗口变动不影响输入框（无边框，prompt_toolkit 自动适配）
4. 初始只显示一行（❯ 提示符 + 光标）
5. 斜杠命令补全（原生 CompletionMenu + Column 渲染）

核心补丁：text_width.install_prompt_toolkit_patch()
prompt_toolkit 的 get_cwidth() 对多字符字符串逐字符求和 wcwidth，
对 ZWJ 序列（👨‍💻 算4列）、VS16 emoji（❤️ 算1列）、旗帜（🇨🇳 算4列）、
肤色修饰（👍🏻 算4列）等字素簇计算错误（终端实际渲染均为2列），
导致光标定位错误 → 退格重叠/错位，且屏幕模型被污染后整个会话持续错位。
本模块在导入时替换其全局宽度缓存为字素簇感知版本，一次替换全局生效。
"""
from __future__ import annotations

from prompt_toolkit import PromptSession
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.document import Document
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.styles import Style
from typing import TYPE_CHECKING

from cbhcli_pkg.core.text_width import (
    display_width, install_prompt_toolkit_patch
)
from cbhcli_pkg.core.resize_fix import install_resize_fix

if TYPE_CHECKING:
    from cbhcli_pkg.commands.parser import SlashCommandParser


def install_toolbar_display_fix():
    """安装底部工具栏即时显示补丁（幂等）

    问题根因：prompt_toolkit 的 bottom_toolbar 容器过滤条件含
    renderer_height_is_known —— 高度要等 CPR（光标位置查询）往返
    完成后才"已知"。若 prompt 启动时输入队列非空（典型场景：AI 回答
    期间用户按了键，type-ahead 残留），
    Application._request_absolute_cursor_position 会跳过 CPR 请求，
    高度永远未知 → 状态栏整轮不显示，直到用户提交/取消进入下一轮
    prompt（新 Application、队列已空、CPR 正常往返）才恢复显示。

    修复思路：将 prompt_toolkit.shortcuts.prompt 模块命名空间中的
    renderer_height_is_known 引用替换为恒真 Condition，使工具栏在
    首轮渲染即显示。安全性：renderer.render() 在高度未知时按
    height = max(0, last_height, preferred_height) 绘制，布局按优选
    高度分配行（提示符行 + 工具栏行），绘制位置正确；CPR 响应到达
    后的高度校正逻辑与原生完全一致（仅少一次"工具栏闪现"延迟）。
    对 CPR 不支持的终端反而修复了工具栏永不显示的问题。

    注意：必须通过 sys.modules 获取真实模块对象！
    `import prompt_toolkit.shortcuts.prompt as m` 会因为 shortcuts
    包 `from .prompt import prompt` 把导出的 prompt() 函数绑定为包
    属性（遮蔽子模块），使 m 绑定到函数而非模块，补丁无效。
    """
    import sys
    from prompt_toolkit.filters import Condition

    # from prompt_toolkit import PromptSession（本文件顶部）已确保
    # prompt_toolkit.shortcuts.prompt 真实模块加载到 sys.modules
    real_pm = sys.modules.get('prompt_toolkit.shortcuts.prompt')
    if real_pm is None:
        import prompt_toolkit.shortcuts.prompt  # noqa: F401
        real_pm = sys.modules['prompt_toolkit.shortcuts.prompt']

    if getattr(real_pm, '_cbhcli_toolbar_fix_patched', False):
        return

    real_pm.renderer_height_is_known = Condition(lambda: True)
    real_pm._cbhcli_toolbar_fix_patched = True


# 模块导入时立即安装字素簇宽度补丁（必须在任何 prompt_toolkit 渲染之前）
install_prompt_toolkit_patch()
# 安装 resize 防抖补丁（修复窗口大小连续变化时输入框重复显示）
install_resize_fix()
# 安装底部工具栏即时显示补丁（修复状态栏偶尔不显示）
install_toolbar_display_fix()


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
    - bottom_toolbar 状态栏：图标 + 高对比配色 + 完整路径
    - 终端 resize 自动适配，无重叠
    - 中英文 / emoji（含 ZWJ/VS16/旗帜/肤色）退格不错位
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

        # 样式（高对比配色，每项信息有独立图标+颜色标识）
        self._style = Style.from_dict({
            'prompt': 'bold #00d7ff',
            'bottom-toolbar': 'noinherit bg:#1c1e24 #999999',
            'bottom-toolbar.label': 'bold #ffffff bg:#1c1e24',
            'bottom-toolbar.model': 'bold #00d7ff bg:#1c1e24',
            'bottom-toolbar.ctx': '#ffd75f bg:#1c1e24',
            'bottom-toolbar.agent': '#ff87d7 bg:#1c1e24',
            'bottom-toolbar.skills': '#87ff87 bg:#1c1e24',
            'bottom-toolbar.cwd': '#87afff bg:#1c1e24',
            'bottom-toolbar.sep': '#444444 bg:#1c1e24',
            # 权限模式指示（Shift+Tab 切换）
            'bottom-toolbar.mode-readonly': 'bold #87afff bg:#1c1e24',
            'bottom-toolbar.mode-standard': 'bold #87ff87 bg:#1c1e24',
            'bottom-toolbar.mode-auto': 'bold #ffd75f bg:#1c1e24',
            'bottom-toolbar.mode-yolo': 'bold #ff5f5f bg:#1c1e24',
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
            erase_when_done=True,  # 提交后自动清除输入行，避免与白底回显重复
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
            from cbhcli_pkg.core.constants import C_SEP, C_RESET
            mode = "详细" if self._app.tool_verbose else "简洁"
            print(f"\n{C_SEP}工具显示: {mode}{C_RESET}")

        @self._bindings.add('s-tab')
        def _cycle_permission_mode(event):
            """Shift+Tab 循环切换权限模式（readonly→standard→auto→yolo）

            进入 yolo 需 3 秒内再按一次 Shift+Tab 确认（防误触）。
            """
            import time as _time
            from prompt_toolkit.application import run_in_terminal
            from cbhcli_pkg.core.permissions import MODES, MODE_META

            app = self._app
            engine = getattr(app, "permission_engine", None)
            if engine is None:
                return

            old_mode = engine.mode
            next_mode = MODES[(MODES.index(old_mode) + 1) % len(MODES)]

            # yolo 二次确认：第一次落入只显示警告并暂存，3 秒内再按确认
            if next_mode == "yolo":
                pending = getattr(self, "_yolo_pending_at", 0)
                if _time.monotonic() - pending > 3:
                    self._yolo_pending_at = _time.monotonic()

                    def _warn():
                        print(f"\n\033[41;97m ⚠️  YOLO 模式将无确认执行一切操作"
                              f"（含 rm/git push），deny 红线降级为警告。 \033[0m")
                        print(f"\033[2m   3 秒内再按一次 Shift+Tab 确认进入 YOLO，"
                              f"否则保持在 {old_mode} 模式\033[0m")
                    run_in_terminal(_warn)
                    event.app.invalidate()
                    return
                self._yolo_pending_at = 0  # 确认，清除暂存
            else:
                self._yolo_pending_at = 0

            new_mode = app.cycle_permission_mode()
            meta = MODE_META[new_mode]

            def _notice():
                print(f"\n{meta['icon']} 权限模式: {old_mode} → {new_mode}"
                      f" — {meta['desc']}")
            run_in_terminal(_notice)
            event.app.invalidate()

    def _build_toolbar(self):
        """构建底部状态栏（单行，图标+亮色标识，完整路径）"""
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
            total_tokens = app.session.get_total_tokens(app.token_counter)
            app.context_window.update(total_tokens)
            pct = app.context_window.usage_percentage() * 100
            limit = app.context_window.model_limit
            ctx_info = f"{total_tokens:,}/{limit:,} ({pct:.1f}%)"

        # 当前路径（完整显示，仅将家目录缩写为 ~，不做省略截断）
        import os
        from pathlib import Path
        cwd = os.getcwd()
        home = str(Path.home())
        if cwd == home:
            cwd = "~"
        elif cwd.startswith(home + os.sep):
            cwd = "~" + cwd[len(home):]

        # Agent名
        agent = app.current_agent_name or ""

        # 技能
        skills_str = ""
        if app.skill_manager:
            active_names = app.skill_manager.get_active_skill_names()
            if active_names:
                skills_str = ','.join(active_names)

        # 构建格式化文本（标签亮白加粗+值独立配色，清晰标识每项信息）
        # 注意：不能用 HTML('<style class="...">')！prompt_toolkit 的 HTML
        # 解析器会丢弃 <style> 标签的 class 属性（html.py 中 "style" 被排除
        # 在 name_stack 之外，仅解析 fg/bg/color 属性），class 永远不会生效。
        # 必须使用 (style, text) 元组列表显式指定 class。
        sep = ("class:bottom-toolbar.sep", " │ ")
        parts: list[tuple[str, str]] = []

        # 权限模式（Shift+Tab 切换，按模式着色）
        # 用 ●（宽1全兼容）代替 emoji 圆点：🟢🟡🔴 等新版 emoji 在部分
        # 终端字体下宽度表缺失/字形缺失，导致后续文字被覆盖或显示豆腐块
        if getattr(app, "permission_engine", None):
            mode = app.permission_engine.mode
            meta = app.permission_engine.mode_meta()
            parts.append((f"class:bottom-toolbar.mode-{mode}",
                          f"● {meta['label']}"))
            parts.append(sep)

        parts.append(("class:bottom-toolbar.label", "模型: "))
        parts.append(("class:bottom-toolbar.model", model_name))
        if ctx_info:
            parts.append(sep)
            parts.append(("class:bottom-toolbar.label", "上下文: "))
            parts.append(("class:bottom-toolbar.ctx", ctx_info))
        if agent:
            parts.append(sep)
            parts.append(("class:bottom-toolbar.label", "Agent: "))
            parts.append(("class:bottom-toolbar.agent", agent))
        if skills_str:
            parts.append(sep)
            parts.append(("class:bottom-toolbar.label", "技能: "))
            parts.append(("class:bottom-toolbar.skills", skills_str))
        parts.append(sep)
        parts.append(("class:bottom-toolbar.label", "路径: "))
        parts.append(("class:bottom-toolbar.cwd", cwd))

        return parts

    def prompt(self) -> str:
        """显示提示符并获取用户输入

        Returns:
            用户输入文本（已 strip），EOFError 时返回 "quit"，KeyboardInterrupt 时返回 ""
        """
        # 同样使用元组列表而非 HTML（<style class> 会被解析器丢弃）
        prompt_text = [("class:prompt", "❯ ")]

        try:
            user_input = self._session.prompt(
                prompt_text,
                prompt_continuation=[("class:prompt", "  ")],
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
        所以直接 print 即可。多行回显的前缀缩进按显示宽度对齐。
        """
        from cbhcli_pkg.core.constants import C_USER_BG, C_USER_FG, C_RESET

        agent = self._app.current_agent_name or ""
        prefix = f"[{agent}] " if agent else ""
        # 按显示宽度对齐（Agent名可能含中文/emoji，len() 不等于显示宽度）
        prefix_pad = ' ' * display_width(prefix)

        lines = user_input.split('\n')
        first_line = lines[0]
        print(f"{C_USER_BG}{C_USER_FG}▌ {prefix}> {first_line}{C_RESET}")
        for line in lines[1:]:
            print(f"{C_USER_BG}{C_USER_FG}  {prefix_pad}  {line}{C_RESET}")
