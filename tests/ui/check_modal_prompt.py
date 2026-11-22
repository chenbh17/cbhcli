#!/usr/bin/env python3
"""门禁：全屏模态提示不得把换行渲染成 `^J`

背景（用户实测）：
    ask_user 工具传的 prompt 带前导换行（`"\\n\\033[34m请选择: \\033[0m"`），
    全屏模态把它当**单行行前缀**（`get_line_prefix`）渲染，
    prompt_toolkit 会把控制字符转成 caret 形式 → 界面上显示成
    `^J请选择: `（用户报"ask_user 工具显示 ^J请回答"）。

修复点：`core/ui/shell.py` 的 `FullScreenUI._format_prompt()` —
剥掉首尾换行、中间换行/制表符折叠成空格。

用法：
    /usr/bin/python3 tests/ui/check_modal_prompt.py
"""

from __future__ import annotations

import io
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

import pyte  # noqa: E402

from prompt_toolkit import Application  # noqa: E402
from prompt_toolkit.data_structures import Size  # noqa: E402
from prompt_toolkit.formatted_text import to_formatted_text  # noqa: E402
from prompt_toolkit.input.defaults import create_pipe_input  # noqa: E402
from prompt_toolkit.layout import Layout  # noqa: E402
from prompt_toolkit.layout.containers import Window  # noqa: E402
from prompt_toolkit.layout.controls import FormattedTextControl  # noqa: E402
from prompt_toolkit.output.vt100 import Vt100_Output  # noqa: E402

from cbhcli_pkg.core.ui.shell import FullScreenUI  # noqa: E402

checks: list[tuple[str, bool]] = []


def check(name: str, cond: bool) -> None:
    checks.append((name, cond))


def fmt(prompt: str) -> str:
    """调用真实的 _format_prompt，拼回纯文本"""
    frags = FullScreenUI._format_prompt(FullScreenUI.__new__(FullScreenUI), prompt)
    return "".join(t for _, t in to_formatted_text(frags))


# ----------------------------------------------------------------------
# 1. 单元断言：控制字符不再出现在提示里
# ----------------------------------------------------------------------
raw_ask_user = "\n\033[34m请选择: \033[0m"
text = fmt(raw_ask_user)
check("前导换行被剥掉（ask_user 场景）", "\n" not in text and text.startswith("请选择: "))
check("颜色码仍被解析（无字面 \\033）", "\033" not in text and "\x1b" not in text)

text_tab = fmt("a\tb")
check("制表符折叠成空格", "\t" not in text_tab and text_tab == "a b")

text_mid = fmt("第一行\n第二行")
check("中间换行折叠成空格", "\n" not in text_mid and text_mid == "第一行 第二行")

check("空提示返回空 fragments", fmt("") == "")
check("纯文本提示原样保留", fmt("链条名称: ") == "链条名称: ")

# ----------------------------------------------------------------------
# 2. 渲染断言：单行行前缀里不能再出现 ^J（pyte 列精确重放）
# ----------------------------------------------------------------------
buf = io.StringIO()
out = Vt100_Output(buf, get_size=lambda: Size(rows=6, columns=60), term="xterm")
prefix = to_formatted_text(FullScreenUI._format_prompt(
    FullScreenUI.__new__(FullScreenUI), raw_ask_user))
win = Window(
    FormattedTextControl(lambda: [("", "1")]),
    get_line_prefix=lambda ln, wrap: prefix,
    wrap_lines=True,
)
with create_pipe_input() as inp:
    app = Application(layout=Layout(win), output=out, input=inp, full_screen=True)
    app.renderer.render(app, app.layout, is_done=False)
data = buf.getvalue()

screen = pyte.Screen(60, 6)
pyte.Stream(screen).feed(data)
line0 = screen.display[0].rstrip()
check("渲染行不含 '^J'", "^J" not in line0)
check("渲染行包含提示文字", "请选择" in line0)
check("渲染行以提示开头（无前导空格）", line0.startswith("请选择"))

# ----------------------------------------------------------------------
# 汇总
# ----------------------------------------------------------------------
print("=" * 70)
print("门禁：全屏模态提示不得显示 ^J")
print("=" * 70)
print(f"  渲染首行 = {line0!r}")
ok = True
for name, cond in checks:
    print(f"  {'✅' if cond else '❌'} {name}")
    ok = ok and cond
print("\n✅ PASS" if ok else "\n❌ FAIL")
raise SystemExit(0 if ok else 1)
