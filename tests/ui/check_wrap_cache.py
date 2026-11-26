#!/usr/bin/env python3
"""v5.5.6 门禁：逐行折行缓存（Screen._wrapped_line / _Line 版本号）

用法：
    /usr/bin/python3 tests/ui/check_wrap_cache.py

背景：
    全屏 TUI 流式期间每次 feed 都使 screen.version+1，上层缓存必失效，
    旧实现每帧对全部逻辑行（最多 5000）重新 wrap_cells+merge_runs，
    内容越多每帧越慢（5000 行实测 ~186ms/帧 → 事件循环阻塞 → Jupyter 终端卡）。
    v5.5.6 改为逐行缓存：只有"变过的行"重折，未变行 O(1) 复用。

覆盖：
    1. 正确性：各种变更后 visual_lines 与"全量新鲜折行"参照逐字符一致
       （追加 / \\r 覆盖 / \\b / 擦除 K、J / 光标移动 / 样式变化 / 宽字符 / resize / 裁剪）
    2. 缓存命中：仅改末行时，未变行不重折（用计数器验证 wrap_cells 调用次数骤降）
    3. 缓存失效：任何原地变更（append/setitem/delitem/clear/erase）都使该行重折
    4. 性能：5000 行会话仅追加 1 行，单帧耗时远低于全量折行
    5. visual_line_count 与 visual_lines 行数一致

契约：docs/全屏重构行为契约.md → T-13/T-17（折行/重排语义不变）
"""

from __future__ import annotations

import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from cbhcli_pkg.core.ui import lines as L  # noqa: E402
from cbhcli_pkg.core.ui.lines import Screen, merge_runs, wrap_cells  # noqa: E402

FAILS: list[str] = []


def check(cond: bool, msg: str) -> None:
    if not cond:
        FAILS.append(msg)


def reference_visual(screen: Screen, width: int):
    """全量新鲜折行参照（不经过任何缓存）"""
    out = []
    for line in screen.lines():          # lines() 返回 plain-list 副本
        for vline in wrap_cells(line, width):
            out.append(merge_runs(vline))
    return out


def assert_equiv(screen: Screen, width: int, tag: str) -> None:
    got = screen.visual_lines(width)
    exp = reference_visual(screen, width)
    check(got == exp, f"[{tag}] visual_lines 与全量参照不一致 (w={width})")
    check(screen.visual_line_count(width) == len(exp),
          f"[{tag}] visual_line_count 与 visual_lines 行数不一致")


# ----------------------------------------------------------------------
#  1) 正确性：各类变更后与参照一致
# ----------------------------------------------------------------------
def correctness():
    s = Screen()
    s.feed("hello world\n")
    assert_equiv(s, 40, "初始")

    # 追加多行
    for i in range(20):
        s.feed(f"line {i} 中文混排 def foo(x): return x+1\n")
    assert_equiv(s, 40, "追加")

    # \r 覆盖（spinner 场景）
    s.feed("\rOVERWRITE")
    assert_equiv(s, 40, "\\r覆盖")

    # \b 退格
    s.feed("\b\b\bXY")
    assert_equiv(s, 40, "\\b退格")

    # 擦除行 K（0/1/2）
    s.feed("\x1b[10C\x1b[K")
    assert_equiv(s, 40, "K0擦除")
    s.feed("\x1b[1K")
    assert_equiv(s, 40, "K1擦除")
    s.feed("\x1b[2K")
    assert_equiv(s, 40, "K2擦除")

    # 光标上移 + 重画（思考窗场景）
    s.feed("\x1b[3A")
    s.feed("rewritten-top")
    assert_equiv(s, 40, "上移重画")
    s.feed("\x1b[J")           # 从光标清到屏末
    assert_equiv(s, 40, "J0擦除")

    # 样式变化（SGR）
    s.feed("\x1b[31mRED\x1b[0m plain \x1b[1;32mBOLD_GREEN\x1b[0m\n")
    assert_equiv(s, 40, "样式")

    # 宽字符 / emoji / ZWJ / tab
    s.feed("中文宽度测试\tTAB\n")
    s.feed("👨‍💻 family 👍🏻 flag 🇨🇳 keycap 1️⃣\n")
    assert_equiv(s, 40, "宽字符")
    assert_equiv(s, 20, "宽字符窄屏")

    # resize：同一内容换宽度
    for w in (10, 20, 60, 120, 78):
        assert_equiv(s, w, f"resize{w}")

    # 绝对定位 + 覆盖
    s.feed("\x1b[5;3HABS")
    assert_equiv(s, 40, "绝对定位")

    # J1 / J2 整屏擦除
    s.feed("\x1b[1J")
    assert_equiv(s, 40, "J1擦除")
    s.feed("\x1b[2J")
    assert_equiv(s, 40, "J2擦除")
    s.feed("after-clear\n")
    assert_equiv(s, 40, "擦除后重写")


# ----------------------------------------------------------------------
#  2) 裁剪（滚动缓冲上限）后仍一致
# ----------------------------------------------------------------------
def trim_correctness():
    s = Screen(max_lines=50)
    for i in range(200):
        s.feed(f"scrollback line {i} 内容\n")
    assert_equiv(s, 40, "裁剪后")
    check(len(s.lines()) <= 50, "裁剪未生效（超过 max_lines）")
    # 裁剪后再追加
    s.feed("new-after-trim\n")
    assert_equiv(s, 40, "裁剪后追加")
    # 缓存剪枝：条目数不应无界增长
    check(len(s._wrap_cache) <= len(s._lines) * 2 + 128,
          "缓存剪枝未生效（条目无界增长）")


# ----------------------------------------------------------------------
#  3) 缓存命中：仅改末行时未变行不重折
# ----------------------------------------------------------------------
def cache_hit():
    s = Screen()
    for i in range(300):
        s.feed(f"stable line {i} with some content 中文\n")
    s.visual_lines(78)                    # 预热缓存

    orig_wrap = L.wrap_cells
    calls = {"n": 0}

    def counting_wrap(line, width):
        calls["n"] += 1
        return orig_wrap(line, width)

    L.wrap_cells = counting_wrap
    try:
        # 仅追加一行（只应折"新行 + 可能的末行"，远小于 300）
        s.feed("brand-new-line\n")
        s.visual_lines(78)
        n_append = calls["n"]
        check(n_append < 30,
              f"追加单行却重折了 {n_append} 行（缓存未命中，应 <30）")

        # 完全无变更：再查一次应为 0 次重折
        calls["n"] = 0
        s.visual_lines(78)
        check(calls["n"] == 0,
              f"无变更却重折了 {calls['n']} 行（应 0）")

        # \r 覆盖末行：只重折末行
        calls["n"] = 0
        s.feed("\rX")
        s.visual_lines(78)
        check(calls["n"] <= 2,
              f"\\r 覆盖末行重折了 {calls['n']} 行（应 ≤2）")
    finally:
        L.wrap_cells = orig_wrap


# ----------------------------------------------------------------------
#  4) 缓存失效：每种原地变更都要让该行重折
# ----------------------------------------------------------------------
def cache_invalidation():
    # 直接构造一个 Screen，手工变更某行，确认结果随之变化
    s = Screen()
    s.feed("abc def ghi\n")
    s.feed("second line here\n")
    v0 = s.visual_lines(40)
    # 通过 \r + 覆盖改变末行
    s.feed("\rZZZ")
    v1 = s.visual_lines(40)
    check(v0 != v1, "末行覆盖后 visual_lines 未变化（缓存陈旧）")
    check(v1 == reference_visual(s, 40), "覆盖后与参照不一致")

    # 擦除某行中间
    s.feed("\x1b[1A")          # 上移到倒数第二行
    s.feed("\x1b[2K")          # 整行擦除
    s.feed("replaced")
    v2 = s.visual_lines(40)
    check(v2 == reference_visual(s, 40), "擦除重写后与参照不一致（缓存陈旧）")


# ----------------------------------------------------------------------
#  5) 性能：5000 行会话仅追加 1 行，单帧远低于全量
# ----------------------------------------------------------------------
def performance():
    body = ("这是流式输出的一行内容，包含中文与 ASCII mixed text 0123456789 "
            "def foo(x): return x + 1  # 代码片段\n")
    s = Screen(max_lines=6000)
    for i in range(5000):
        s.feed(body)
    s.visual_lines(78)                    # 预热

    # 全量新鲜折行基准
    t0 = time.perf_counter()
    reference_visual(s, 78)
    full_ms = (time.perf_counter() - t0) * 1000

    # 追加一行后的缓存帧（重复多次取均值）
    reps = 20
    t0 = time.perf_counter()
    for _ in range(reps):
        s.feed("x")                       # 仅改末行
        s.visual_lines(78)
    cached_ms = (time.perf_counter() - t0) / reps * 1000

    print(f"    5000 行：全量折行 {full_ms:.1f}ms/帧 → 缓存帧 {cached_ms:.2f}ms/帧"
          f"（加速 {full_ms / max(cached_ms, 1e-9):.0f}×）")
    check(cached_ms < full_ms * 0.25,
          f"缓存帧 {cached_ms:.2f}ms 未显著快于全量 {full_ms:.1f}ms（应 <25%）")


def main():
    correctness()
    trim_correctness()
    cache_hit()
    cache_invalidation()
    performance()

    if FAILS:
        print(f"\n❌ FAIL（{len(FAILS)} 项）")
        for f in FAILS:
            print(" -", f)
        return 1
    print("\n✅ PASS（逐行折行缓存：正确性/命中/失效/裁剪/性能全部通过）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
