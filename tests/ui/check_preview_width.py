#!/usr/bin/env python3
"""工具代码预览宽度回归（v5.5.0）

用法：
    /usr/bin/python3 tests/ui/check_preview_width.py

背景（用户实测）：全屏 TUI 主区文字宽度 = 终端列数 − 2，而工具预览用
rich 按终端满宽绘制 → 最右 1~2 列被主区折到下一行最左侧，表现为
edit 差异预览"右侧绿色内容换行到左边、红绿错位"。

断言：edit 差异预览 / 代码预览的输出**每一行显示宽度 ≤ 主区宽度**，
即主区不会再折行（不会错位）。
"""
from __future__ import annotations

import io
import os
import re
import sys
import types
from contextlib import redirect_stdout

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[a-zA-Z]")


def _load_tool_executor():
    import cbhcli_pkg  # noqa: F401
    core_pkg = types.ModuleType("cbhcli_pkg.core")
    core_pkg.__path__ = [os.path.join(ROOT, "cbhcli_pkg", "core")]
    sys.modules["cbhcli_pkg.core"] = core_pkg
    from cbhcli_pkg.core import tool_executor as te
    return te


class FakeUI:
    def __init__(self, text_width: int):
        self._w = text_width

    def text_width(self) -> int:
        return self._w


def render(te, fn, ui_width):
    """用假 UI 宽度渲染，返回去 ANSI 的行列表"""
    fake_mod = types.ModuleType("cbhcli_pkg.core.ui.shell")
    fake_mod.get_active_ui = lambda: FakeUI(ui_width)
    sys.modules["cbhcli_pkg.core.ui.shell"] = fake_mod
    buf = io.StringIO()
    with redirect_stdout(buf):
        fn()
    out = ANSI_RE.sub("", buf.getvalue())
    return out.split("\n")


def main() -> int:
    te = _load_tool_executor()
    from cbhcli_pkg.core.text_width import display_width

    executor = te.ToolExecutor.__new__(te.ToolExecutor)
    executor._EDIT_MAX_DISPLAY = 200
    executor._EDIT_SIDE_BY_SIDE_MIN = 110
    executor._CODE_MAX_DISPLAY = 200

    ok = True
    for ui_width in (60, 80, 120):
        # 主区显示宽度 = ui_width；渲染必须 ≤ ui_width-1
        limit = ui_width - 1
        # 构造长行（超过列宽，必然折行）+ 行内差异
        long_old = ["x = 1  # " + "很长的中文注释" * 12]
        long_new = ["x = 2  # " + "很长的中文注释" * 12]
        lines = render(
            te,
            lambda: executor._render_edit_preview(
                long_old, long_new, 10, "a.py"),
            ui_width)
        widths = [display_width(x) for x in lines]
        over = [w for w in widths if w > limit]
        print(f"edit 预览 @主区{ui_width}: 最长行={max(widths)} "
              f"允许={limit} {'✅' if not over else '❌ 超宽 ' + str(over[:5])}")
        ok = ok and not over

        code = "\n".join("print('行 %d' + '很长的内容' * 10)" % i
                         for i in range(5))
        lines = render(
            te,
            lambda: executor._render_code_preview(code, "执行代码", "python"),
            ui_width)
        widths = [display_width(x) for x in lines]
        over = [w for w in widths if w > limit]
        print(f"代码预览 @主区{ui_width}: 最长行={max(widths)} "
              f"允许={limit} {'✅' if not over else '❌ 超宽 ' + str(over[:5])}")
        ok = ok and not over

    print("\n✅ PASS" if ok else "\n❌ FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
