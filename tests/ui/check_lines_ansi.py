#!/usr/bin/env python3
"""P2 门禁：逻辑行模型 + ANSI 解析/编码 + 256 色保真

用法：
    python tests/ui/check_lines_ansi.py

覆盖：
    1. SGR 与 prompt_toolkit 独立解析器交叉校验（颜色/属性）
    2. xterm 256 色表逐项校验（0~253 与 PT 表一致；254/255 用标准值）
    3. Screen 覆盖语义：\\r / \\b / \\033[K / \\033[2K / \\033[A+\\033[J
    4. 思考窗口"上移擦除重画"模拟（不得出现重复内容）
    5. 折行：ASCII / 中文 / ZWJ emoji / tab
    6. resize 重排：文本不丢不重
    7. 滚动缓冲上限

契约：docs/全屏重构行为契约.md → T-05/T-06/T-11/T-13/T-17、V-18b~e、V-67
"""

from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from cbhcli_pkg.core.ui.ansi import (  # noqa: E402
    ANSI16_NAMES,
    ANSI16_RGB,
    PALETTE256,
    DEFAULT_STYLE,
    ansi_to_segments,
    palette_rgb,
    parse_sgr,
    segments_to_ansi,
    style_to_pt_style,
)
from cbhcli_pkg.core.ui.lines import Cell, Screen, merge_runs, wrap_cells  # noqa: E402

FAILS: list[str] = []


def check(cond: bool, msg: str) -> None:
    if not cond:
        FAILS.append(msg)


def _text_of(vis) -> list[str]:
    return ["".join(t for t, _ in line) for line in vis]


# ----------------------------------------------------------------------
#  1) SGR 交叉校验（prompt_toolkit 当参照实现）
# ----------------------------------------------------------------------
from prompt_toolkit.styles import Style as _PTStyle  # noqa: E402

_PT_STYLE = _PTStyle.from_dict({})


def _pt_attrs_from_ansi(s: str):
    from prompt_toolkit.formatted_text import ANSI, to_formatted_text

    frags = to_formatted_text(ANSI(s))
    style_str = frags[-1][0] if frags else ""
    return _PT_STYLE.get_attrs_for_style_str(style_str)


def _our_attrs(style):
    return _PT_STYLE.get_attrs_for_style_str(style_to_pt_style(style))


def _color_rgb(c):
    if c is None or c == "":
        return None
    if isinstance(c, str):
        raw = c[1:] if c.startswith("#") else c
        if len(raw) == 6:
            try:
                return (int(raw[0:2], 16), int(raw[2:4], 16), int(raw[4:6], 16))
            except ValueError:
                pass
    name_map = {n: i for i, n in enumerate(ANSI16_NAMES)}
    if c in name_map:
        return ANSI16_RGB[name_map[c]]
    return ("?", c)


def sgr_cross_check() -> None:
    corpus = [
        "\x1b[0mX",
        "\x1b[31mX",
        "\x1b[91mX",
        "\x1b[38;5;196mX",
        "\x1b[38;5;240mX",
        "\x1b[48;5;240mX",
        "\x1b[1;4;7mX",
        "\x1b[2;3;9mX",
        "\x1b[38;2;1;2;3m\x1b[48;2;4;5;6mX",
        "\x1b[31mred\x1b[0mX",
        "\x1b[38;5;0mX",
        "\x1b[38;5;15mX",
        "\x1b[1m\x1b[22mX",
        "\x1b[38;5;196;1mX",
    ]
    for s in corpus:
        segs = ansi_to_segments(s)
        ours = segs[-1][1] if segs else DEFAULT_STYLE
        pt = _pt_attrs_from_ansi(s)
        oa = _our_attrs(ours)
        label = repr(s)
        check(_color_rgb(oa.color) == _color_rgb(pt.color), f"前景色不一致: {label} ours={oa.color} pt={pt.color}")
        check(_color_rgb(oa.bgcolor) == _color_rgb(pt.bgcolor), f"背景色不一致: {label} ours={oa.bgcolor} pt={pt.bgcolor}")
        for flag in ("bold", "dim", "italic", "underline", "reverse", "strike", "blink"):
            check(getattr(oa, flag) == getattr(pt, flag), f"属性 {flag} 不一致: {label}")


def palette_check() -> None:
    from prompt_toolkit.output.vt100 import _256_colors as pt_quant

    pt = pt_quant.colors
    # 标准 xterm 232/254/255 值（PT 的量化表有 off-by-one，见下方说明）
    std_expected = {232: (8, 8, 8), 254: (228, 228, 228), 255: (238, 238, 238)}
    for n in range(256):
        r, g, b = palette_rgb(n)
        if n in std_expected:
            check((r, g, b) == std_expected[n], f"调色板 {n} 标准值错误: ours={(r,g,b)}")
        else:
            check((r, g, b) == tuple(pt[n]), f"调色板 {n} 与 PT 不一致: ours={(r,g,b)} pt={pt[n]}")

    # 已知偏差（prompt_toolkit 量化表 off-by-one / 忽略低灰度）：
    #   232(8,8,8)  → 16(0,0,0)     每通道 Δ8
    #   254(228)    → 253(218)      每通道 Δ10
    #   255(238)    → 231(255,255,255) 每通道 Δ17
    # 其余 253 个索引必须**精确**回到自身。
    allowed = {232: 16, 254: 253, 255: 231}
    for n in range(16, 256):
        got = pt_quant[palette_rgb(n)]
        if n in allowed:
            check(got == allowed[n], f"已知偏差索引 {n} 的量化结果变了: {got}")
        else:
            check(got == n, f"256 色量化不精确: {n} → {got}")

    # 编码往返（语义）
    for n in (0, 1, 15, 16, 231, 232, 240, 254, 255):
        s = f"\x1b[38;5;{n}mX"
        segs = ansi_to_segments(s)
        back = ansi_to_segments(segments_to_ansi(segs))
        check(back[-1][1] == segs[-1][1], f"256 色编码往返不一致: n={n}")

    # n<16 必须保留为 ansi 名（否则会被量化到立方体色）
    for n in (0, 1, 7, 9, 15):
        style = ansi_to_segments(f"\x1b[38;5;{n}mX")[-1][1]
        check(style.fg == ("ansi", n), f"38;5;{n} 未归一为 ansi 色: {style.fg}")


# ----------------------------------------------------------------------
#  2) 覆盖语义
# ----------------------------------------------------------------------
def screen_ops() -> None:
    s = Screen()
    s.feed("abc\rX")
    check(s.text() == "Xbc", f"\\r 覆盖失败: {s.text()!r}")

    s = Screen()
    s.feed("abcdef\r\x1b[Kxy")
    check(s.text() == "xy", f"\\033[K 截断失败: {s.text()!r}")

    # 终端语义：2K 只清行、不动光标 → 不先 \r 的话新内容写在原列（补空格）
    s = Screen()
    s.feed("abcdef\r\x1b[2Kxy")
    check(s.text() == "xy", f"\\r+\\033[2K 清行失败: {s.text()!r}")
    s = Screen()
    s.feed("abcdef\x1b[2Kxy")
    check(s.text() == "      xy", f"\\033[2K 光标位置语义错误: {s.text()!r}")

    s = Screen()
    s.feed("abc\b\bX")
    check(s.text() == "aXc", f"\\b 退格失败: {s.text()!r}")

    # 上移 + 擦除到屏末（思考窗口的核心序列）
    s = Screen()
    s.feed("a\nb\nc\n")
    s.feed("\x1b[3A\x1b[J")
    check(s.plain_lines() == [""], f"\\033[A+\\033[J 未清空: {s.plain_lines()!r}")
    s.feed("X\nY\nZ\n")
    check(s.plain_lines() == ["X", "Y", "Z", ""], f"重画结果错误: {s.plain_lines()!r}")

    # spinner 两帧：\r + 新内容 + \033[K，不得折成两行
    s = Screen()
    s.feed("  ⠋ 执行中 terminal 0.1s")
    s.feed("\r\x1b[K  ⠙ 执行中 terminal 0.2s")
    check(s.text() == "  ⠙ 执行中 terminal 0.2s", f"spinner 覆盖失败: {s.text()!r}")
    check(s.visual_line_count(40) == 1, "spinner 覆盖产生了多余行")

    # process 监控状态行：输出一行 + \r\033[2K 重画状态行
    s = Screen()
    s.feed("task output\n")
    s.feed("\r\x1b[2K⏱ 已运行 5s | Ctrl+C 停止监控")
    check(s.plain_lines() == ["task output", "⏱ 已运行 5s | Ctrl+C 停止监控"],
          f"process 状态行覆盖失败: {s.plain_lines()!r}")


def thinking_window() -> None:
    """模拟 ThinkingDisplay：头行 + 8 行，两帧重画后不得重复"""
    s = Screen()
    header1 = "💭 思考中... 3s"
    content1 = [f"line{i}" for i in range(8)]
    s.feed(header1 + "\n" + "\n".join(content1) + "\n")

    # 下一帧：上移 9 行（头 + 8 内容），擦除到屏末，再画
    s.feed(f"\x1b[{1 + len(content1)}A\x1b[J")
    header2 = "💭 思考完毕 3s"
    content2 = [f"line{i}" for i in range(8)]
    s.feed(header2 + "\n" + "\n".join(content2) + "\n")

    lines = s.plain_lines()
    check(len(lines) == 10, f"思考窗口行数应为 10，实际 {len(lines)}: {lines!r}")
    check(lines[0] == header2, f"头行未替换: {lines[0]!r}")
    check("思考中" not in s.text(), f"旧窗口残留: {s.text()!r}")
    check(lines.count("line0") == 1, f"内容重复: {lines!r}")


# ----------------------------------------------------------------------
#  3) 折行 / resize
# ----------------------------------------------------------------------
def wrapping() -> None:
    s = Screen()
    s.feed("abcdefghij")
    check(_text_of(s.visual_lines(4)) == ["abcd", "efgh", "ij"],
          f"ASCII 折行错误: {_text_of(s.visual_lines(4))!r}")

    s = Screen()
    s.feed("中文abc")
    check(_text_of(s.visual_lines(4)) == ["中文", "abc"],
          f"中文折行(4)错误: {_text_of(s.visual_lines(4))!r}")
    check(_text_of(s.visual_lines(3)) == ["中", "文a", "bc"],
          f"中文折行(3)错误: {_text_of(s.visual_lines(3))!r}")

    s = Screen()
    s.feed("👨‍👩‍👧‍👦x")
    check(_text_of(s.visual_lines(2)) == ["👨‍👩‍👧‍👦", "x"],
          f"ZWJ emoji 折行(2)错误: {_text_of(s.visual_lines(2))!r}")
    check(_text_of(s.visual_lines(3)) == ["👨‍👩‍👧‍👦x"],
          f"ZWJ emoji 折行(3)错误: {_text_of(s.visual_lines(3))!r}")

    s = Screen()
    s.feed("a\tb")
    check(s.text() == "a   b", f"tab 展开错误: {s.text()!r}")

    # 样式跨行保留
    s = Screen()
    s.feed("\x1b[31mabcdef\x1b[0m")
    vis = s.visual_lines(3)
    check(len(vis) == 2 and all(style.fg == ("ansi", 1) for line in vis for _, style in line),
          f"折行后样式丢失: {vis!r}")


def resize_reflow() -> None:
    src = "abcdefghijklmnopqrstuvwxyz0123456789"
    s = Screen()
    s.feed(src)
    at80 = "".join(_text_of(s.visual_lines(80)))
    at10 = "".join(_text_of(s.visual_lines(10)))
    at7 = "".join(_text_of(s.visual_lines(7)))
    check(at80 == src, f"宽 80 折行丢内容: {at80!r}")
    check(at10 == src, f"宽 10 折行丢内容: {at10!r}")
    check(at7 == src, f"宽 7 折行丢内容: {at7!r}")
    check(len(_text_of(s.visual_lines(10))) == 4, f"宽 10 行数错误: {_text_of(s.visual_lines(10))!r}")

    # 多逻辑行：换宽度不合并逻辑行
    s = Screen()
    s.feed("ab\ncd\n")
    check(_text_of(s.visual_lines(1)) == ["a", "b", "c", "d", ""],
          f"多行重排错误: {_text_of(s.visual_lines(1))!r}")


def scrollback_limit() -> None:
    s = Screen(max_lines=3)
    for i in range(6):
        s.feed(f"L{i}\n")
    lines = s.plain_lines()
    check(len(lines) == 3, f"缓冲上限失败: {lines!r}")
    non_empty = [x for x in lines if x]
    check(non_empty[-1] == "L5", f"缓冲上限保留内容错误: {lines!r}")
    check(non_empty[0] == "L4", f"缓冲上限丢弃数量错误: {lines!r}")
    check(s.row <= 3, f"缓冲上限后 row 未调整: {s.row}")


def main() -> int:
    print("=" * 70)
    print("P2 门禁：逻辑行模型 + ANSI/256 色")
    print("=" * 70)

    sgr_cross_check()
    palette_check()
    screen_ops()
    thinking_window()
    wrapping()
    resize_reflow()
    scrollback_limit()

    if FAILS:
        print(f"\n❌ FAIL（{len(FAILS)} 项）")
        for f in FAILS:
            print(" -", f)
        return 1
    print("\n✅ PASS（SGR/256 色与 prompt_toolkit 交叉一致；覆盖语义/折行/重排/上限全部通过）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
