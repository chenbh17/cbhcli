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
from prompt_toolkit.styles import Style

from cbhcli_pkg.core.text_width import install_prompt_toolkit_patch

# 确保字素簇宽度补丁已安装
install_prompt_toolkit_patch()

# 共享的简单会话（无补全、单行为主，也支持粘贴多行）
_session = PromptSession(
    style=Style.from_dict({
        'prompt': 'bold #00d7ff',
    }),
)


def ask_text(prompt: str, default: str = "") -> str:
    """统一的单行文本输入

    Args:
        prompt: 提示文本（如 "模型名称: "）
        default: 用户直接回车或取消(EOF/Ctrl+C)时返回的默认值

    Returns:
        用户输入（已 strip）；空输入返回 default
    """
    try:
        result = _session.prompt([('class:prompt', prompt)])
        result = result.strip()
        return result if result else default
    except (EOFError, KeyboardInterrupt):
        return default


def ask_text_or_none(prompt: str) -> "str | None":
    """统一的单行文本输入（取消时返回 None，用于确认类场景）

    Args:
        prompt: 提示文本

    Returns:
        用户输入（已 strip，可能为空字符串）；EOF/Ctrl+C 取消时返回 None
    """
    try:
        return _session.prompt([('class:prompt', prompt)]).strip()
    except (EOFError, KeyboardInterrupt):
        return None
