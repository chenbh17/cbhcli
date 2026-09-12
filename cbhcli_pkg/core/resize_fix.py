"""终端 resize 修复（v5.3.2 第四轮：消除 resize 后 ❯ 残留）

【问题现象】
终端窗口拖小后，输入框出现"一串 ❯"（每次改窗口残留一个旧 ❯）。
在真实终端仿真台（xterm.js，与 Jupyter 终端同类）上 100% 复现：
旧代码 ❯ 逐次累积 2→3→4→5，新代码恒为 1。

【根因】
终端 resize 后会 reflow（长行重折 + 滚屏），不同终端对"光标"的处理不同：
  · 真实终端（VTE/xterm/iTerm 等）：光标跟随内容 → CPR 行可信；
  · xterm.js 类（Jupyter/VS Code 终端）：光标停在旧行、不跟随内容
    → CPR 完全不可信（旧代码正是用 CPR 推"旧区域顶"，于是每 resize 残留一个 ❯）。
另外多行输入在某宽度下折行数变化时，光标在区域内的偏移必须按"新宽度"
重算，不能用上一帧旧值（否则偏 1~N 行：偏上擦内容 / 偏下留 ❯）。

【本版方案：双路估算 + 陈旧光标识别】
  a) est_cpr  = CPR 光标行 − 偏移（偏移用干渲染按新宽度重算）→ 光标跟随内容时准确；
  b) est_prev = 上次区域顶 − max(0, 区域自身 reflow 增长 − 区域空白间隙)
     → 区域顶部固定假设下的保守估算（间隙会先吸收折行增量）；
  c) est_full = 上次区域顶 − 区域自身 reflow 增长
     → 终端底部固定（滚屏）时，区域顶恰好上移这么多行。
  判定：CPR 行号 == 旧光标行（"光标根本没动"）→ 该终端光标不跟随内容，
  用 est_full（宁可多清也不能残留 ❯）；否则用 min(est_cpr, est_prev)。
  区域自身 reflow 增长用"逐行/同逻辑行拼接后按新宽度折行"模型估算，
  并且只统计"真的会写进终端"的格子（空白且样式无可见属性不算）。

【沿用第三轮的两条硬约束（勿回退）】
  1. 重绘范围 == 清除范围（清除「起点 ~ 屏幕底部」，渲染高度锁成同一行数）。
  2. 渲染高度全程锁定，避免渲染器 `\\r\\n` 向下移动触发滚动。
  3. 绝不主动滚屏、绝不把区域上移；清除严格从清除起点往下。

【调试】CBHCLI_RESIZE_DEBUG=1 → /tmp/cbhcli_resize_debug.log
"""
from __future__ import annotations

import asyncio
import os

# 防抖间隔（秒）：拖动停止后才处理一次
_RESIZE_DEBOUNCE_SEC = 0.10
# CPR 响应等待超时（秒）：超时则只用 est_prev 清除
_CPR_TIMEOUT_SEC = 0.35
# 拿不到上次渲染高度时的默认区域行数
_DEFAULT_REGION_ROWS = 2

_DEBUG = os.environ.get('CBHCLI_RESIZE_DEBUG') == '1'
_DEBUG_PATH = '/tmp/cbhcli_resize_debug.log'


def _log(msg: str):
    if not _DEBUG:
        return
    try:
        import time
        with open(_DEBUG_PATH, 'a', encoding='utf-8') as f:
            f.write(f"[{time.strftime('%H:%M:%S')}] {msg}\n")
    except Exception:
        pass


def _raw(output, seq: str) -> bool:
    """向终端写原始转义序列；返回是否成功"""
    try:
        write_raw = getattr(output, 'write_raw', None)
        if write_raw is None:
            return False
        write_raw(seq)
        return True
    except Exception:
        return False


def _flush(output):
    try:
        output.flush()
    except Exception:
        pass


def _screen_size(output):
    """屏幕 (高, 宽)"""
    scr_h = scr_w = 0
    try:
        scr_h, scr_w = output.get_size()
    except Exception:
        pass
    if not scr_h or not scr_w:
        try:
            size = os.get_terminal_size()
            scr_w, scr_h = size.columns, size.lines
        except Exception:
            scr_w, scr_h = 80, 24
    return max(int(scr_h), 2), max(int(scr_w), 2)


def _natural_pref(app, cols: int, rows: int) -> int:
    """布局在当前宽度下的自然优选高度（= 输入框内容高度 H）

    注意：必须调用"未被本补丁夹过"的原始 preferred_height，
    否则拿到的是上一轮 resize 锁定后的值。
    """
    try:
        container = app.layout.container
        func = getattr(container, '_cbhcli_orig_pref', None)
        if func is None:
            func = container.preferred_height
        value = func(cols, rows) if callable(func) else func
        # preferred_height() 返回 Dimension，取 .preferred
        value = getattr(value, 'preferred', value)
        return max(1, int(value or 0))
    except Exception as exc:
        _log(f"_natural_pref 失败: {exc!r}")
        return 0


def _last_region_height(renderer, scr_h: int, fallback: int) -> int:
    """上次渲染的区域行数（来自 _last_screen.height，不是 _last_size！）"""
    last_h = 0
    try:
        last_screen = getattr(renderer, '_last_screen', None)
        if last_screen is not None:
            last_h = int(getattr(last_screen, 'height', 0) or 0)
    except Exception:
        pass
    if last_h <= 0 or last_h > scr_h:
        last_h = int(fallback or _DEFAULT_REGION_ROWS)
    return max(1, min(last_h, scr_h))


def _dry_caret_offset(app, cols: int, height: int, fallback: int = 0) -> int:
    """离屏渲染一次布局，取输入光标在区域内的相对行（0-based）

    用途：resize 后输入可能在更窄宽度下折成更多行，光标所在行
    （= 旧区域顶往下第几行）必须按新宽度计算，不能用上一帧的旧值。
    """
    try:
        from prompt_toolkit.layout.screen import Screen, WritePosition
        from prompt_toolkit.layout.mouse_handlers import MouseHandlers

        layout = app.layout
        screen = Screen()
        layout.container.write_to_screen(
            screen, MouseHandlers(),
            WritePosition(xpos=0, ypos=0, width=max(int(cols), 2),
                          height=max(int(height), 1)),
            parent_style='', erase_bg=False, z_index=None,
        )
        window = getattr(layout, 'current_window', None)
        if window is None:
            return max(0, int(fallback))
        point = screen.get_cursor_position(window)
        return max(0, int(getattr(point, 'y', 0) or 0))
    except Exception as exc:
        _log(f"干渲染取光标偏移失败: {exc!r}")
        return max(0, int(fallback))


def _row_span(screen, y: int, style_cache=None) -> int:
    """上一帧第 y 行"真正写进终端"的内容宽度（列数，0 = 空行）

    只有"真正会写进终端"的格子才算内容 —— 空白且样式无可见属性
    （如 `[transparent] class:last-line`）的格子渲染器不会写，终端里是空行尾。
    """
    try:
        row = screen.data_buffer[y]
    except Exception:
        return 0
    span = 0
    try:
        for col, cell in row.items():
            char = getattr(cell, 'char', ' ')
            style = getattr(cell, 'style', '')
            if char == ' ':
                if not style:
                    continue
                visible = None
                if style_cache is not None:
                    try:
                        visible = bool(style_cache[style])
                    except Exception:
                        visible = None
                if visible is None:
                    visible = ('[transparent]' not in style)
                if not visible:
                    continue
            width = int(getattr(cell, 'width', 1) or 1)
            if col + width > span:
                span = col + width
    except Exception:
        pass
    return span


def _row_sig(screen, y: int, style_cache=None):
    """行签名：第一个"可见格"的样式（用于判断哪些行属于同一逻辑行的折行）"""
    try:
        row = screen.data_buffer[y]
    except Exception:
        return None
    try:
        for col in sorted(row.keys()):
            cell = row[col]
            char = getattr(cell, 'char', ' ')
            style = getattr(cell, 'style', '')
            if char == ' ':
                visible = None
                if style_cache is not None:
                    try:
                        visible = bool(style_cache[style])
                    except Exception:
                        visible = None
                if visible is None:
                    visible = bool(style) and '[transparent]' not in style
                if not visible:
                    continue
            return style
    except Exception:
        pass
    return None


def _box_reflow(screen, new_cols: int, style_cache=None):
    """估算"上一帧区域"在新宽度下的 reflow 情况

    返回 (growth, gap)：
      growth = 区域内容按"同一逻辑行拼接后重折"估算多出来的行数
      gap    = 区域内"有内容行"之间的空白行数（这些空白行能先吸收折行增量）

    终端（xterm.js 类）实测规律：
      · 同一逻辑行的折行会整体重折（比逐行独立折行更贴近终端）；
      · 区域自身内容变高时，多余的折行先被区域内的空白间隙吸收；
      · 吸收不完的部分才会把区域顶整体顶上去（终端底部固定）。
    """
    if screen is None or new_cols <= 0:
        return 0, 0
    try:
        rows = int(getattr(screen, 'height', 0) or 0)
    except Exception:
        return 0, 0
    if rows <= 0:
        return 0, 0
    spans = [_row_span(screen, y, style_cache) for y in range(rows)]
    sigs = [_row_sig(screen, y, style_cache) for y in range(rows)]

    # 按"样式相同的连续有内容行"分组 = 同一逻辑行的折行（输入折行 / 工具栏折行）
    groups: list[list[int]] = []
    cur: list[int] = []
    cur_sig = None
    for y, span in enumerate(spans):
        if span > 0:
            if cur and sigs[y] != cur_sig:
                groups.append(cur)
                cur = []
            cur.append(span)
            cur_sig = sigs[y]
        elif cur:
            groups.append(cur)
            cur = []
    if cur:
        groups.append(cur)

    reflowed = 0
    for group in groups:
        joined = sum(group)
        reflowed += max(1, -(-joined // int(new_cols)))    # ceil(joined / new_cols)
    first = next((i for i, s in enumerate(spans) if s > 0), None)
    last = next((i for i in range(rows - 1, -1, -1) if spans[i] > 0), None)
    if first is None or last is None:
        return 0, 0
    gap = sum(1 for i in range(first, last + 1) if spans[i] == 0)
    drawn = last - first + 1                    # 有内容行 + 其间空白行
    reflowed += gap                             # 空白行 reflow 后仍是每行 1 行
    _log(f"  box_reflow: 屏高={rows} 首={first} 尾={last} spans={spans[:rows]} "
         f"组={[len(g) for g in groups]} reflow后={reflowed} 原={drawn}")
    return max(0, reflowed - drawn), gap


def install_resize_fix():
    """安装 resize 修复补丁（幂等）"""
    from prompt_toolkit.application import Application
    from prompt_toolkit.renderer import Renderer

    if getattr(Application, '_cbhcli_resize_patched', False):
        return

    try:
        from prompt_toolkit.layout.dimension import Dimension
    except Exception:
        Dimension = None  # type: ignore
    try:
        from prompt_toolkit.data_structures import Point
    except Exception:
        Point = None  # type: ignore

    _orig_on_resize = Application._on_resize
    _orig_render = Renderer.render
    _orig_report = Renderer.report_absolute_cursor_row

    # ==================================================================
    # 1) Renderer.render：记录布局自然高度 + 锁定渲染高度
    # ==================================================================
    def _render_wrapper(self, app, layout, *args, **kwargs):
        # ⚠️ 必须透传 *args/**kwargs：prompt_toolkit 的 Renderer.render 还有
        # 第三个参数 is_done（Application 收尾时以 is_done=True 渲染最后一帧，
        # 见 application.py 的 `renderer.render(self, self.layout, is_done=...)`）。
        # 旧版把签名写死成 (self, app, layout)，导致 PromptSession.run() 结束
        # 时报 "unexpected keyword argument 'is_done'" —— 所有走 prompt_utils
        # 的交互式命令（/resume 菜单、/undo-compress、/mcp add 等）都会
        # "命令执行失败"（v5.3.3 修复）。
        # 注意：主输入框不受影响（erase_when_done=True 走 renderer.erase()）。
        try:
            size = self.output.get_size()
            natural = _natural_pref(app, size.columns, size.rows)
            if natural:
                self._cbhcli_pref_h = natural
            self._cbhcli_last_w = int(size.columns)
        except Exception:
            pass
        try:
            limit = int(getattr(self, '_cbhcli_render_limit', 0) or 0)
            if limit > 0:
                # 锁定 _min_available_height（height = max(_min_available_height, ...)）
                if int(getattr(self, '_min_available_height', 0) or 0) != limit:
                    self._min_available_height = limit
                # 夹布局 preferred 到同一上限（只夹 preferred，保留 min/max）
                if Dimension is not None:
                    container = layout.container
                    if not hasattr(container, '_cbhcli_orig_pref'):
                        container._cbhcli_orig_pref = container.preferred_height
                        container._cbhcli_orig_min = container.min_height
                        container._cbhcli_orig_max = container.max_height
                    o_pref = container._cbhcli_orig_pref
                    o_min = container._cbhcli_orig_min
                    o_max = container._cbhcli_orig_max

                    def _clamped(_o=o_pref, _mn=o_min, _mx=o_max, _lim=limit):
                        try:
                            v = int((_o() if callable(_o) else _o) or 0)
                        except Exception:
                            v = 0
                        try:
                            return Dimension(min=_mn, max=_mx, preferred=min(v, _lim))
                        except Exception:
                            return Dimension(preferred=min(v, _lim))

                    container.preferred_height = _clamped
        except Exception:
            pass
        _orig_render(self, app, layout, *args, **kwargs)

    # ==================================================================
    # 2) 清除 + 重绘：重绘范围 == 清除范围（核心）
    # ==================================================================
    def _apply_clear(app, clear_start, new_pref_h=0):
        """清除 clear_start(1-based) ~ 屏幕底部，并让渲染正好填满这一范围

        区域可用高度 = 清除范围行数，因此渲染必然从 clear_start 开始、
        逐行向下填满，不会有"未被覆盖的行"。
        """
        output = getattr(app, 'output', None)
        renderer = getattr(app, 'renderer', None)
        if output is None or renderer is None:
            return False

        scr_h, scr_w = _screen_size(output)
        clear_start = max(1, min(int(clear_start), scr_h))
        avail = scr_h - clear_start + 1

        # 清除 clear_start ~ 屏幕底部（先定位到第 1 列，否则只清该行光标之后）
        if not _raw(output, f'\x1b[{clear_start};1H'):
            return False
        try:
            output.erase_down()
        except Exception:
            _raw(output, '\x1b[J')
        # 光标回到清除起点：渲染将从这里开始向下填满
        _raw(output, f'\x1b[{clear_start};1H')
        _flush(output)

        # 重置渲染状态并锁定高度（顺序：reset 会清 _min_available_height，故之后设）
        try:
            renderer.reset()
        except Exception:
            pass
        try:
            renderer._cbhcli_render_limit = avail
            renderer._min_available_height = avail
            if new_pref_h:
                renderer._cbhcli_pref_h = int(new_pref_h)
        except Exception:
            pass
        if Point is not None:
            try:
                renderer._cursor_pos = Point(0, 0)
            except Exception:
                pass
        try:
            app.invalidate()
        except Exception:
            pass

        _log(f"清除重绘: 屏幕={scr_w}x{scr_h} 清除起点={clear_start} 区域行数={avail}")
        return True

    # ==================================================================
    # 3) CPR 响应：拿真实光标行 → 与 est_prev 合并后清除
    # ==================================================================
    def _report_wrapper(self, row, *args, **kwargs):
        try:
            _orig_report(self, row, *args, **kwargs)
        except Exception:
            pass

        info = getattr(self, '_cbhcli_resize_info', None)
        app = getattr(self, '_cbhcli_app', None)
        if not info or app is None:
            return
        est_prev, new_pref_h, seq = info[0], info[1], info[2]
        top_prev = info[3] if len(info) > 3 else est_prev
        offset_old = info[4] if len(info) > 4 else 0
        w_old = info[5] if len(info) > 5 else 0
        w_new = info[6] if len(info) > 6 else w_old
        est_full = int(info[7]) if len(info) > 7 else est_prev
        if seq != getattr(app, '_cbhcli_resize_seq', -1):
            return                       # 过期响应，忽略
        self._cbhcli_resize_info = None

        output = getattr(app, 'output', None)
        scr_h = scr_w = 0
        try:
            scr_h, scr_w = _screen_size(output)
        except Exception:
            scr_h, scr_w = 24, 80

        clear_start = est_prev
        try:
            # 新宽度下光标在区域内的偏移（干渲染），用于反推旧区域顶
            guess_avail = max(1, scr_h - int(est_prev) + 1)
            caret_off = _dry_caret_offset(app, scr_w, guess_avail, fallback=offset_old)
            est_cpr = int(row) - caret_off
            # 光标行仍是"旧光标行"（没动过）→ 说明该终端 reflow 后光标不跟随内容
            # （xterm.js 类，含 Jupyter 终端），CPR 不可信 → 用区域顶估算兜底。
            stale = (int(row) == int(top_prev) + int(offset_old))
            if stale:
                # 陈旧：区域自身增长已把区域顶顶上去（终端底部固定 + 上方内容
                # reflow 时终端必滚屏 → 按完整增长估算，宁可多清也不能残留 ❯）
                clear_start = est_full
                _log(f"resize[CPR]: 光标行={row} 未动(陈旧) 偏移={caret_off} "
                     f"est_cpr={est_cpr} est_prev={est_prev} est_full={est_full} "
                     f"→ 清除起点={clear_start}")
            else:
                # 光标已跟随内容：以 CPR 反推的区域顶为准；若它比保守估算更低
                # （终端光标有 ±1 抖动），取更高者，避免残留旧 ❯
                clear_start = min(int(est_cpr), int(est_prev))
                _log(f"resize[CPR]: 光标行={row} 偏移={caret_off} "
                     f"est_cpr={est_cpr} est_prev={est_prev} → 清除起点={clear_start}")
        except Exception as exc:
            _log(f"CPR 估算异常: {exc!r}")
        except Exception as exc:
            _log(f"CPR 估算异常: {exc!r}")

        try:
            _apply_clear(app, clear_start, new_pref_h)
        except Exception as exc:
            _log(f"清除失败: {exc!r}")

    # ==================================================================
    # 4) Application._on_resize：防抖 → 估算旧区域顶 → reset + 发 CPR
    # ==================================================================
    def _debounced_on_resize(self, *args, **kwargs):
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            try:
                _orig_on_resize(self, *args, **kwargs)
            except Exception:
                pass
            return

        handle = getattr(self, '_cbhcli_resize_handle', None)
        if handle is not None:
            try:
                handle.cancel()
            except Exception:
                pass

        def _do_resize():
            self._cbhcli_resize_handle = None
            try:
                _start_resize(self, loop)
            except Exception as exc:
                _log(f"resize 启动异常: {exc!r}")
                try:
                    renderer = getattr(self, 'renderer', None)
                    fallback = getattr(renderer, '_cbhcli_last_clear', 1) or 1
                    _apply_clear(self, fallback)
                except Exception:
                    try:
                        _orig_on_resize(self, *args, **kwargs)
                    except Exception:
                        pass

        self._cbhcli_resize_handle = loop.call_later(_RESIZE_DEBOUNCE_SEC, _do_resize)

    def _start_resize(app, loop):
        renderer = getattr(app, 'renderer', None)
        output = getattr(app, 'output', None)
        if renderer is None or output is None:
            _orig_on_resize(app)
            return

        scr_h, scr_w = _screen_size(output)

        # ---- 估算一：上次渲染的区域顶 - 区域自身 reflow 增长（终端底部固定） ----
        new_pref_h = _natural_pref(app, scr_w, scr_h) or 0
        old_pref_h = int(getattr(renderer, '_cbhcli_pref_h', 0) or 0) or new_pref_h
        last_region_h = _last_region_height(renderer, scr_h, new_pref_h)
        top_prev = scr_h - last_region_h + 1

        # 区域自身内容 reflow 增长：逐行折行（含样式补齐空格，如工具栏整行底色）；
        # 区域内的空白间隙会先吸收这部分增量，吸收不完才会把区域顶整体顶上去
        # （终端底部固定）。实测：按间隙折减才能既清掉旧 ❯ 又不误擦上方内容。
        try:
            style_cache = getattr(renderer, '_style_string_has_style', None)
            growth, gap = _box_reflow(getattr(renderer, '_last_screen', None),
                                      scr_w, style_cache)
        except Exception as exc:
            _log(f"reflow 估算失败: {exc!r}")
            growth, gap = max(0, int(new_pref_h) - int(old_pref_h)), 0
        shift_box = max(0, int(growth) - int(gap))
        est_prev = top_prev - shift_box
        # 区域顶不可能低于"底部对齐"位置（行号上界）
        if new_pref_h:
            est_prev = min(est_prev, scr_h - min(int(new_pref_h), scr_h) + 1)
        est_prev = max(1, min(est_prev, scr_h))
        # 陈旧光标（终端 reflow 后不跟随内容）时用的完整增长估算：
        # 终端底部固定 + 上方内容 reflow 触发滚屏 → 区域顶恰好上移"完整增长"行数
        est_full = max(1, min(int(top_prev) - int(growth), scr_h))

        # 旧光标在区域内的偏移（判断 CPR 是否"没动过"用，reset 会清）
        try:
            offset_old = int(getattr(renderer._cursor_pos, 'y', 0) or 0)
        except Exception:
            offset_old = 0
        w_old = int(getattr(renderer, '_cbhcli_last_w', 0) or scr_w) or scr_w

        app._cbhcli_resize_seq = seq = getattr(app, '_cbhcli_resize_seq', 0) + 1
        renderer._cbhcli_app = app
        renderer._cbhcli_resize_info = (est_prev, new_pref_h, seq,
                                        top_prev, offset_old, w_old, scr_w, est_full)

        _log(f"resize[start]: 屏幕={scr_w}x{scr_h}(旧宽={w_old}) 区域内容高={new_pref_h}"
             f"(旧={old_pref_h}) 上次区域高={last_region_h} reflow增长={growth} "
             f"间隙={gap} 区域顶(旧)={top_prev} 光标偏移={offset_old} "
             f"est_prev={est_prev} est_full={est_full}")

        # reset 把 _cursor_pos 归零（request_absolute_cursor_position 断言 y==0）
        try:
            renderer.reset()
        except Exception:
            pass

        cpr_sent = False
        try:
            renderer.request_absolute_cursor_position()
            cpr_sent = bool(getattr(renderer, 'waiting_for_cpr', False))
        except Exception as exc:
            _log(f"request_absolute_cursor_position 异常: {exc!r}")

        # 无 CPR（终端不支持/不可用）→ 与"陈旧光标"同策略：按完整增长估算清除起点
        fallback_start = int(est_full)

        if not cpr_sent:
            _log(f"resize: CPR 不可用 → 用估算清除 起点={fallback_start} (seq={seq})")
            _apply_clear(app, fallback_start, new_pref_h)
            return

        _log(f"resize: 发起 CPR seq={seq}")

        def _timeout():
            try:
                info = getattr(renderer, '_cbhcli_resize_info', None)
                if info and info[2] == seq:
                    _log(f"resize: CPR 超时 → 用估算清除 起点={fallback_start} (seq={seq})")
                    renderer._cbhcli_resize_info = None
                    _apply_clear(app, fallback_start, new_pref_h)
            except Exception:
                pass

        try:
            loop.call_later(_CPR_TIMEOUT_SEC, _timeout)
        except Exception:
            pass

    # ==================================================================
    # 安装
    # ==================================================================
    try:
        Renderer.render = _render_wrapper
        Renderer.report_absolute_cursor_row = _report_wrapper
        Application._on_resize = _debounced_on_resize
        Application._cbhcli_resize_patched = True
    except Exception:
        try:
            Renderer.render = _orig_render
            Renderer.report_absolute_cursor_row = _orig_report
            Application._on_resize = _orig_on_resize
        except Exception:
            pass
