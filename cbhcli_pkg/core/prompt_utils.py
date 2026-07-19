"""统一交互输入助手 — 所有交互式提问均使用 prompt_toolkit

解决问题：
Python 内置 input() 使用 readline 或终端规范模式（canonical mode），
对中英文混合/emoji 输入的退格处理与 prompt_toolkit 主输入框不一致，
导致 /model add 等交互式命令中输入中文后退格重叠、光标错位。

本模块提供基于 prompt_toolkit 的统一单行输入（复用字素簇宽度补丁），
替代所有内置 input() 调用。
"""
from __future__ import annotations

from prompt_toolkit import PromptSession
from prompt_toolkit.formatted_text import ANSI
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.styles import Style

from cbhcli_pkg.core.text_width import install_prompt_toolkit_patch

# 确保字素簇宽度补丁已安装
install_prompt_toolkit_patch()

# Esc 键绑定：触发 KeyboardInterrupt（等价 Ctrl+C 取消）
# prompt_toolkit 默认 Esc 是 meta 前缀键，不取消输入；
# 单行提问场景 Esc 无其他用途，显式绑定为取消更符合用户直觉
_esc_bindings = KeyBindings()


@_esc_bindings.add('escape')
def _(event):
    event.app.exit(exception=KeyboardInterrupt)


# 共享的简单会话（无补全、单行为主，也支持粘贴多行）
_session = PromptSession(
    style=Style.from_dict({
        'prompt': 'bold #00d7ff',
    }),
    key_bindings=_esc_bindings,
)


def _to_formatted(prompt: str):
    """将提示文本转换为 prompt_toolkit formatted text

    - 含 ANSI 转义码（如 "\\033[34m请选择: \\033[0m"）→ ANSI() 包装解析
      （直接放元组里 ANSI 码不会被解析，会原样显示为 ^[[34m 乱码）
    - 普通文本 → class:prompt 样式（青色加粗）
    - 空字符串 → 空提示（裸输入）
    """
    if not prompt:
        return ""
    if '\033[' in prompt or '\x1b[' in prompt:
        return ANSI(prompt)
    return [('class:prompt', prompt)]


def ask_text(prompt: str = "", default: str = "") -> str:
    """统一的单行文本输入

    Args:
        prompt: 提示文本（如 "模型名称: "），可为空（裸输入）；
                支持内嵌 ANSI 颜色码（自动解析）
        default: 用户直接回车或取消(EOF/Ctrl+C)时返回的默认值

    Returns:
        用户输入（已 strip）；空输入返回 default
    """
    try:
        result = _session.prompt(_to_formatted(prompt))
        result = result.strip()
        return result if result else default
    except (EOFError, KeyboardInterrupt):
        return default


def ask_text_or_none(prompt: str = "") -> "str | None":
    """统一的单行文本输入（取消时返回 None，用于确认类场景）

    Args:
        prompt: 提示文本，可为空；支持内嵌 ANSI 颜色码（自动解析）

    Returns:
        用户输入（已 strip，可能为空字符串）；EOF/Ctrl+C 取消时返回 None
    """
    try:
        return _session.prompt(_to_formatted(prompt)).strip()
    except (EOFError, KeyboardInterrupt):
        return None
