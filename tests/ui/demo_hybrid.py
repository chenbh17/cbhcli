#!/usr/bin/env python3
"""P3 演示：全屏主区 + 输入区 + 状态栏（不接真实模型）

用法：
    python tests/ui/demo_hybrid.py

功能：
    - 输入任意文本回车 → 模拟流式输出（40 行 + 思考窗口 + 工具行 + 活动行）
    - `/clear` 清空主区视图；`quit` 退出
    - PgUp/PgDn 翻页；Ctrl+Home/Ctrl+End 到顶/到底；Ctrl+L 清屏
    - 输入 `/` 触发补全菜单（演示 Style/补全菜单接入）
    - 运行中按 Ctrl+C 取消本轮（输入区保持可编辑，A1 语义）

说明：这是 P3 的**验证用外壳**，真实 `CBHCLIApp` 的接线在 P4 完成。
"""

from __future__ import annotations

import asyncio
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from prompt_toolkit.completion import WordCompleter  # noqa: E402

from cbhcli_pkg.core.thinking_display import ThinkingDisplay  # noqa: E402
from cbhcli_pkg.core.ui.shell import FullScreenUI  # noqa: E402
from cbhcli_pkg.core.ui.sink import get_sink  # noqa: E402

STATE = {"cancel": False}


def status_provider():
    pct = 12.3
    tokens = 15744
    return [
        ("class:bottom-toolbar.mode-standard", "● 标准"),
        ("class:bottom-toolbar.sep", " │ "),
        ("class:bottom-toolbar.label", "模型: "),
        ("class:bottom-toolbar.model", "p3-demo"),
        ("class:bottom-toolbar.sep", " │ "),
        ("class:bottom-toolbar.label", "上下文: "),
        ("class:bottom-toolbar.ctx", f"████░░░░░░ {tokens:,}/128,000 ({pct:.1f}%)"),
        ("class:bottom-toolbar.sep", " │ "),
        ("class:bottom-toolbar.label", "路径: "),
        ("class:bottom-toolbar.cwd", os.getcwd()),
        ("class:bottom-toolbar.sep", " │ "),
        ("class:bottom-toolbar.label", "PgUp/PgDn 滚动 · quit 退出"),
    ]


async def stream_turn(ui: FullScreenUI, text: str) -> None:
    ui.set_busy(True)
    STATE["cancel"] = False
    try:
        ui.feed(f"❯ {text}\n")

        # 思考窗口（真实 ThinkingDisplay 走 SINK → Screen）
        td = ThinkingDisplay(max_lines=8)
        td.enabled = True
        td.start_thinking()
        for i in range(12):
            if STATE["cancel"]:
                break
            td.add_content(f"思考片段 {i}: 正在分析这个请求，检查上下文与工具…\n")
            await asyncio.sleep(0.03)
        td.finish_thinking()

        # 正文（带颜色 + 中文 + emoji）
        for i in range(40):
            if STATE["cancel"]:
                break
            ui.feed(
                f"\033[37mAI: \033[0m第 {i} 行普通输出；"
                f"\033[36m颜色\033[0m；中文/emoji: 🔐 ● ▸\n"
            )
            if i == 5:
                # 中间标记行 + 停顿，确保被渲染出来（供 pty 冒烟测试匹配）
                ui.feed("[mid-stream]\n")
                await asyncio.sleep(0.5)
            else:
                await asyncio.sleep(0.02)

        if not STATE["cancel"]:
            # 工具行 + 活动行（语义 API，经 Screen 解释 \r / \033[K）
            ui.feed("\n\033[90m" + "─" * 40 + "\033[0m\n")
            ui.feed("\033[32m○ terminal\033[0m  echo hello\n")
            sink = get_sink()
            t0 = time.monotonic()
            for i in range(8):
                if STATE["cancel"]:
                    break
                sink.live_line(
                    f"  \033[32m⠋\033[0m 执行中 terminal \033[2m{time.monotonic() - t0:.1f}s\033[0m"
                )
                await asyncio.sleep(0.08)
            sink.live_line(erase=True)
            ui.feed("\033[32m  ● 完成\033[0m \033[2m· 0.7s\033[0m\n")
            ui.feed("\033[36m  ⎿  hello\033[0m\n")
            ui.feed("\033[90m" + "─" * 40 + "\033[0m\n\n")

        if STATE["cancel"]:
            ui.feed("\n\033[2m操作被中断。\033[0m\n")
        else:
            # 整行新内容（便于 pty 冒烟测试做原子匹配）
            ui.feed("\n[stream-done]\n")
    finally:
        ui.set_busy(False)


def main() -> int:
    words = WordCompleter(
        ["quit", "/clear", "/help", "/model", "/tools", "/resume", "/ctx"],
        ignore_case=True,
    )

    ui = FullScreenUI(
        status_provider=status_provider,
        completer=words,
    )

    def on_submit(text: str) -> None:
        if text in ("quit", "exit"):
            ui.app.exit()
            return
        if text == "/clear":
            ui.clear_main()
            return
        asyncio.get_running_loop().create_task(stream_turn(ui, text))

    def on_interrupt() -> None:
        if ui.busy:
            STATE["cancel"] = True
        else:
            ui.app.exit()

    ui.on_submit = on_submit
    ui.on_interrupt = on_interrupt

    ui.feed("CBHCLI P3 全屏 Demo（输入文本回车 / PgUp 滚动 / quit 退出）\n\n")
    asyncio.run(ui.run_async())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
