#!/usr/bin/env python3
"""emoji 宽度终端自适应门禁（v5.5.2 滚动条残影修复）

用法：
    /usr/bin/python3 tests/ui/check_emoji_width.py

背景：prompt_toolkit 差分重绘按"模型宽度"推进光标。xterm.js（JupyterLab/
VS Code 终端）把 emoji 渲染为 1 列，而模型默认 2 列 → 行内光标跟踪失配 →
滚动条多重残影/双轨道/文字尾巴错乱。修复：启动时 CPR 实测终端 emoji 宽度
（probe_emoji_width），自适应设置 _EMOJI_WIDTH。

覆盖：
    1. 默认 mode=2：emoji=2、CJK=2/字、几何符号=1
    2. set_emoji_width(1)：emoji=1，CJK/几何符号/ASCII 不变（仅 emoji 类自适应）
    3. 切回 2 恢复
    4. VS16 簇 / ZWJ 簇随 mode；旗帜恒 2
    5. probe_emoji_width 非 TTY 安全返回（不抛异常）
    6. pt 宽度缓存随 mode 刷新（get_cwidth 一致）

真实终端渲染正确性由 CDP xterm.js 实测验证（/tmp/verify_fix_xterm.py，
滚动/resize 后滚动条单一连续、无残影）。
"""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from cbhcli_pkg.core import text_width as tw  # noqa: E402


def main() -> int:
    ok = True

    def check(name, cond):
        nonlocal ok
        print(("  ✅ " if cond else "  ❌ ") + name)
        ok = ok and cond

    tw.set_emoji_width(2)
    check("默认 emoji=2", tw.display_width("🔧") == 2)
    check("默认 ✅=2", tw.display_width("✅") == 2)
    check("CJK 你好=4", tw.display_width("你好") == 4)
    check("ASCII abc=3", tw.display_width("abc") == 3)
    check("几何符号 ●│█=3", tw.display_width("●│█") == 3)
    check("旗帜恒 2", tw.display_width("🇨🇳") == 2)

    tw.set_emoji_width(1)
    check("mode1 emoji=1", tw.display_width("🔧") == 1)
    check("mode1 ✅=1", tw.display_width("✅") == 1)
    check("mode1 📦=1", tw.display_width("📦") == 1)
    check("mode1 CJK 不变=4", tw.display_width("你好") == 4)
    check("mode1 几何符号不变=3", tw.display_width("●│█") == 3)
    check("mode1 ASCII 不变=3", tw.display_width("abc") == 3)
    check("mode1 混合行", tw.display_width("🔧 LINE-001 ✅") == 1 + 1 + 8 + 1 + 1)
    check("mode1 VS16 簇=1", tw.display_width("❤️") == 1)
    check("mode1 旗帜仍 2", tw.display_width("🇨🇳") == 2)
    # v5.5.3：xterm.js 不做字素簇合并，组合簇按码点求和（CDP CPR 实测值）
    check("mode1 ZWJ 👨‍💻=2", tw.display_width("👨‍💻") == 2)
    check("mode1 肤色 👍🏻=2", tw.display_width("👍🏻") == 2)
    check("mode1 家庭 👨‍👩‍👧‍👦=4", tw.display_width("👨‍👩‍👧‍👦") == 4)
    check("mode1 keycap 1️⃣=1", tw.display_width("1️⃣") == 1)
    check("mode1 彩虹旗 🏳️‍🌈=2", tw.display_width("🏳️‍🌈") == 2)

    # pt 缓存一致
    try:
        from prompt_toolkit.utils import get_cwidth
        tw.install_prompt_toolkit_patch()
        check("pt get_cwidth(🔧)=1（缓存已刷新）", get_cwidth("🔧") == 1)
    except Exception as e:
        check(f"pt 缓存一致（{e}）", False)

    tw.set_emoji_width(2)
    check("切回 2 恢复", tw.display_width("🔧") == 2)

    # probe 非 TTY 安全
    try:
        r = tw.probe_emoji_width()
        check("probe 非 TTY 安全返回", r in (1, 2))
    except Exception as e:
        check(f"probe 非 TTY 不抛异常（{e}）", False)

    print("\n" + ("✅ PASS" if ok else "❌ FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
