"""UI 层（全屏重构）

当前阶段：P1 —— 输出统一入口（透传），见 `docs/全屏重构行为契约.md` §12。

模块规划（后续阶段逐步加入）：
    sink.py       输出统一入口（P1，已完成）
    lines.py      逻辑行模型 / ANSI→样式片段（P2）
    shell.py      全屏 Application + 三层布局（P3）
    modal.py      应用内模态输入（P5b）
"""

from cbhcli_pkg.core.ui.sink import (  # noqa: F401
    SinkWriter,
    UI_MODES,
    get_sink,
    install,
    is_installed,
    ui_mode,
    uninstall,
)

__all__ = [
    "SinkWriter",
    "UI_MODES",
    "get_sink",
    "install",
    "is_installed",
    "ui_mode",
    "uninstall",
]
