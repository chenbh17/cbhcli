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

import os

from prompt_toolkit import PromptSession
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.document import Document
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.styles import Style
from typing import TYPE_CHECKING

from cbhcli_pkg.core.text_width import (
    display_width, grapheme_clusters, install_prompt_toolkit_patch
)
from cbhcli_pkg.core.resize_fix import install_resize_fix

if TYPE_CHECKING:
    from cbhcli_pkg.commands.parser import SlashCommandParser


def _clip_head(text: str, max_width: int) -> str:
    """截取头部（显示宽度 <= max_width，按字素簇）"""
    out = ""
    w = 0
    for cluster in grapheme_clusters(text):
        cw = display_width(cluster)
        if w + cw > max_width:
            break
        out += cluster
        w += cw
    return out


def _clip_tail(text: str, max_width: int) -> str:
    """截取尾部（显示宽度 <= max_width，按字素簇）"""
    out = []
    w = 0
    for cluster in reversed(grapheme_clusters(text)):
        cw = display_width(cluster)
        if w + cw > max_width:
            break
        out.append(cluster)
        w += cw
    return "".join(reversed(out))


def _ellipsize_middle(text: str, max_width: int) -> str:
    """中间省略（保留头尾，如 ~/a…/last），用于状态栏单块超宽截断"""
    if max_width <= 0:
        return ""
    if display_width(text) <= max_width:
        return text
    if max_width == 1:
        return "…"
    avail = max_width - 1  # 省略号占 1 列
    head_budget = avail // 2
    tail_budget = avail - head_budget
    return _clip_head(text, head_budget) + "…" + _clip_tail(text, tail_budget)


def _clip_comma_list(text: str, max_width: int) -> str:
    """逗号列表超宽截断：保留头部完整项名 + ",…"（尽量不切断项名）"""
    if max_width <= 0:
        return ""
    if display_width(text) <= max_width:
        return text
    if max_width <= 2:
        return "…"
    head = _clip_head(text, max_width - 2)
    cut = head.rfind(",")
    if cut >= max(1, len(head) // 2):
        head = head[:cut]
    return head + ",…"


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
# 全屏/内联都需要（emoji/CJK 宽度是渲染层的通用问题）
install_prompt_toolkit_patch()

# 以下两个补丁只针对"内联 PromptSession + 相对擦除"：
#   - resize_fix：估算区域位置修复 resize 残留 ❯
#   - toolbar fix：CPR 未返回时状态栏整轮不显示
# 全屏模式（CBHCLI_UI=fullscreen）不使用 PromptSession，故跳过（P7 清理）
try:
    from cbhcli_pkg.core.ui.sink import ui_mode as _ui_mode
    _INLINE_PATCHES = _ui_mode() != "fullscreen"
except Exception:
    _INLINE_PATCHES = True

if _INLINE_PATCHES:
    install_resize_fix()
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


# 聊天输入框配色（唯一来源，H-11：全屏输入区/状态栏必须复用同一份）
CHAT_INPUT_STYLE_DICT = {
    'prompt': 'bold #00d7ff',
    'bottom-toolbar': 'noinherit bg:#1c1e24 #999999',
    'bottom-toolbar.label': 'bold #ffffff bg:#1c1e24',
    'bottom-toolbar.model': 'bold #00d7ff bg:#1c1e24',
    'bottom-toolbar.ctx': '#ffd75f bg:#1c1e24',
    'bottom-toolbar.agent': '#ff87d7 bg:#1c1e24',
    'bottom-toolbar.chain': 'bold #ffaf00 bg:#1c1e24',
    'bottom-toolbar.skills': '#87ff87 bg:#1c1e24',
    'bottom-toolbar.cwd': '#87afff bg:#1c1e24',
    'bottom-toolbar.sep': '#444444 bg:#1c1e24',
    # 权限模式指示（Shift+Tab 切换）
    'bottom-toolbar.mode-readonly': 'bold #87afff bg:#1c1e24',
    'bottom-toolbar.mode-standard': 'bold #87ff87 bg:#1c1e24',
    'bottom-toolbar.mode-auto': 'bold #ffd75f bg:#1c1e24',
    'bottom-toolbar.mode-yolo': 'bold #ff5f5f bg:#1c1e24',
    # 上下文占用条（D6-14）
    'bottom-toolbar.bar-ok': '#87ff87 bg:#1c1e24',
    'bottom-toolbar.bar-warn': '#ffd75f bg:#1c1e24',
    'bottom-toolbar.bar-hot': '#ff5f5f bg:#1c1e24',
    'bottom-toolbar.bar-empty': '#444444 bg:#1c1e24',
    'completion-menu': 'bg:#333333',
    'completion-menu.completion': 'bg:#333333 #cccccc',
    'completion-menu.completion.current': 'bold bg:#00bcd4 #000000',
    'completion-menu.meta.completion': 'bg:#333333 #888888',
    'completion-menu.meta.completion.current': 'bg:#00bcd4 #444444',
}


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
        # 注意：字典已提取为模块级 CHAT_INPUT_STYLE_DICT（H-11 单一来源）
        self._style = Style.from_dict(CHAT_INPUT_STYLE_DICT)

        # 快捷键
        self._bindings = KeyBindings()
        self._setup_keybindings()

        # PromptSession
        self._session = PromptSession(
            style=self._style,
            key_bindings=self._bindings,
            completer=self._completer,
            complete_style='column',  # 单列补全菜单（命令行按一列显示）
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

                    # 全屏 TUI：写主区（X-01）；旧内联模式：run_in_terminal
                    from cbhcli_pkg.core.ui.shell import get_active_ui
                    if get_active_ui() is not None:
                        _warn()
                    else:
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

            from cbhcli_pkg.core.ui.shell import get_active_ui
            if get_active_ui() is not None:
                _notice()
            else:
                run_in_terminal(_notice)
            event.app.invalidate()

    def _build_toolbar(self):
        """构建底部状态栏（自适应终端宽度：超宽自动分行 + 单块超宽截断）

        技能等条目过多时整行超宽，而 toolbar 不换行、直接裁掉右侧，
        导致路径等信息"没有位置显示"。修复：按信息块分行（块内不拆分，
        行尾不截断），单块超宽时中间省略。
        """
        app = self._app

        # 模型名（显示配置名称：同一模型 ID 可有多份不同参数的配置）
        model_name = "未配置模型"
        if app.llm_client and hasattr(app.llm_client, 'config_name'):
            model_name = app.llm_client.config_name
        elif app.llm_client and hasattr(app.llm_client, 'model_name'):
            model_name = app.llm_client.model_name
        elif app.current_agent_config and app.current_agent_config.primary_model:
            model_name = app.current_agent_config.primary_model

        # 上下文信息（D6-14：条 + 数值文本；500ms 节流，禁止每帧全量分词）
        import time as _time
        ctx_info = ""
        ctx_pct = 0.0
        if app.session and app.context_window:
            now = _time.monotonic()
            ts, c_tokens, c_limit, c_pct = getattr(
                self, "_ctx_cache", (0.0, 0, 0, 0.0))
            if now - ts >= 0.5 or c_limit <= 0:
                c_tokens = app.session.get_total_tokens(app.token_counter)
                app.context_window.update(c_tokens)
                c_pct = app.context_window.usage_percentage() * 100
                c_limit = app.context_window.model_limit
                self._ctx_cache = (now, c_tokens, c_limit, c_pct)
            total_tokens, limit, pct = c_tokens, c_limit, c_pct
            ctx_info = f"{total_tokens:,}/{limit:,} ({pct:.1f}%)"
            ctx_pct = pct

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

        # 构建信息块（每块 = [(style, text), ...]，块内不拆分）
        # 注意：不能用 HTML('<style class="...">')！prompt_toolkit 的 HTML
        # 解析器会丢弃 <style> 标签的 class 属性（html.py 中 "style" 被排除
        # 在 name_stack 之外，仅解析 fg/bg/color 属性），class 永远不会生效。
        # 必须使用 (style, text) 元组列表显式指定 class。
        blocks: list[list[tuple[str, str]]] = []

        # 权限模式（Shift+Tab 切换，按模式着色）
        # 用 ●（宽1全兼容）代替 emoji 圆点：🟢🟡🔴 等新版 emoji 在部分
        # 终端字体下宽度表缺失/字形缺失，导致后续文字被覆盖或显示豆腐块
        if getattr(app, "permission_engine", None):
            mode = app.permission_engine.mode
            meta = app.permission_engine.mode_meta()
            blocks.append([(f"class:bottom-toolbar.mode-{mode}",
                            f"● {meta['label']}")])

        blocks.append([("class:bottom-toolbar.label", "模型: "),
                       ("class:bottom-toolbar.model", model_name)])
        if ctx_info:
            # D6-14：10 格占用条（█/░），阈值 60%/80%；数值文本保持原色
            filled = int(round(ctx_pct / 10))
            if ctx_pct > 0 and filled < 1:
                filled = 1
            filled = max(0, min(10, filled))
            if ctx_pct >= 80:
                bar_cls = "bottom-toolbar.bar-hot"
            elif ctx_pct >= 60:
                bar_cls = "bottom-toolbar.bar-warn"
            else:
                bar_cls = "bottom-toolbar.bar-ok"
            block = [("class:bottom-toolbar.label", "上下文: ")]
            if filled:
                block.append((f"class:{bar_cls}", "█" * filled))
            if filled < 10:
                block.append(("class:bottom-toolbar.bar-empty",
                              "░" * (10 - filled)))
            block.append(("class:bottom-toolbar.ctx", " " + ctx_info))
            blocks.append(block)
        if agent:
            # 链条绑定时显示链条路径，否则显示普通 Agent 名
            chain = getattr(app, '_active_chain', None)
            if chain:
                path = getattr(app, '_chain_active_path', None) or [agent]
                chain_str = f"🔗 {chain.name} › {' › '.join(path)}"
                blocks.append([("class:bottom-toolbar.label", "链条: "),
                               ("class:bottom-toolbar.chain", chain_str)])
            else:
                blocks.append([("class:bottom-toolbar.label", "Agent: "),
                               ("class:bottom-toolbar.agent", agent)])
        if skills_str:
            blocks.append([("class:bottom-toolbar.label", "技能: "),
                           ("class:bottom-toolbar.skills", skills_str)])
        blocks.append([("class:bottom-toolbar.label", "路径: "),
                       ("class:bottom-toolbar.cwd", cwd)])

        return self._layout_toolbar_blocks(blocks, self._toolbar_max_width())

    # ------------------------------------------------------------------
    #  底部状态栏布局（自适应宽度）
    # ------------------------------------------------------------------

    def _toolbar_max_width(self) -> int:
        """状态栏可用宽度（按 prompt_toolkit 实际输出宽度，回退终端宽度）"""
        try:
            from prompt_toolkit.application import get_app
            size = get_app().output.get_size()
            if size.columns >= 20:
                return size.columns - 1
        except Exception:
            pass
        try:
            return os.get_terminal_size().columns - 1
        except (ValueError, OSError):
            return 79

    @staticmethod
    def _parts_width(parts) -> int:
        """格式化文本片段的显示宽度"""
        return sum(display_width(text) for _, text in parts)

    def _layout_toolbar_blocks(self, blocks, max_width: int):
        """把信息块按终端宽度分行（块内不拆分），超宽块中间省略截断

        返回格式化文本（片段的文本中插入 "\\n" 实现多行 toolbar；
        prompt_toolkit 的 toolbar 窗口高度自适应内容行数）。
        """
        sep = ("class:bottom-toolbar.sep", " │ ")
        sep_w = display_width(sep[1])
        # 单块超宽时（一整行也放不下）先截断其值部分
        single_limit = max(8, max_width - sep_w)
        fixed: list[list[tuple[str, str]]] = []
        for block in blocks:
            if self._parts_width(block) > single_limit:
                block = self._clip_block(block, single_limit)
            fixed.append(block)
        # 逐块填充行（行首不加分隔符，放不下则换行）
        lines: list[list[tuple[str, str]]] = []
        cur: list[tuple[str, str]] = []
        cur_w = 0
        for block in fixed:
            bw = self._parts_width(block)
            if cur and cur_w + sep_w + bw > max_width:
                lines.append(cur)
                cur, cur_w = [], 0
            if cur:
                cur.append(sep)
                cur_w += sep_w
            cur.extend(block)
            cur_w += bw
        if cur:
            lines.append(cur)
        # 组装为带换行的格式化文本
        out: list[tuple[str, str]] = []
        for i, line in enumerate(lines):
            if i:
                out.append(("", "\n"))
            out.extend(line)
        return out

    def _clip_block(self, block, limit: int):
        """块超宽时截断其值部分（标签保留）

        值形如逗号列表（技能等）时按项名对齐截断（保留完整项名 + ",…"），
        否则做中间省略（保留头尾，如路径）。
        """
        if len(block) < 2:
            return block
        if len(block) > 2:
            # 多片段块（如上下文条：标签+填充+空白+数值）：超宽时退化为
            # "标签 + 截断纯文本"，避免只截到条形图而丢失数值
            label = block[0]
            value_text = "".join(t for _, t in block[1:]).lstrip()
            budget = limit - display_width(label[1])
            if budget < 4:
                return [label]
            return [label, ("class:bottom-toolbar.ctx",
                            _ellipsize_middle(value_text, budget))]
        label, value = block[0], block[1]
        budget = limit - display_width(label[1])
        if budget < 4:
            return [label]
        if value[1].count(",") >= 2:
            new_value = _clip_comma_list(value[1], budget)
        else:
            new_value = _ellipsize_middle(value[1], budget)
        return [label, (value[0], new_value)]

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
