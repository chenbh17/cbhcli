"""逻辑行模型（P2）—— 一个够用的"迷你终端"

契约位置：`docs/全屏重构行为契约.md`
  - D1-04/06  流式输出、思考窗口的内容模型
  - T-11      `\\r` / `\\b` / `\\033[K` / `\\033[2K` / `\\033[A` 覆盖语义
  - T-17      resize 时按新宽度重新折行（等价终端 reflow）
  - T-13      滚动缓冲上限（5000 行）

设计：
  - `Screen` 维护 `lines[row][col]` 的字符网格（按**字素簇**存，宽字符宽 2）；
  - `feed(text)` 消费 ANSI 流：SGR 只影响样式，光标类 CSI 执行移动/擦除；
  - `visual_lines(width)` 按当前宽度做**显示宽度折行**（不拆宽字符），
    所以 resize 只需换一个 width 重新调用（T-17）；
  - 样式与字符同格存储，折行时按相同样式合并成片段，供全屏渲染器直接使用。

已知近似（仅影响极端输入，实际流中不出现）：
  - 光标列按**字素簇下标**计（`\\b` / 部分覆盖宽字符时与终端列不同）；
    现有流里使用 `\\r` / `\\033[K` 的都是 ASCII 进度行（spinner、process），不受影响。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

from cbhcli_pkg.core.text_width import display_width, grapheme_clusters
from cbhcli_pkg.core.ui.ansi import (
    DEFAULT_STYLE,
    Style,
    iter_csi,
    parse_sgr,
)

__all__ = ["Cell", "Screen", "wrap_cells", "merge_runs"]

MAX_SCROLLBACK_LINES = 5000


@dataclass(frozen=True)
class Cell:
    """一个显示单元（字素簇）"""

    ch: str
    style: Style = DEFAULT_STYLE
    width: int = 1


def _cells_text(line: Sequence[Cell]) -> str:
    return "".join(c.ch for c in line)


def merge_runs(line: Sequence[Cell]) -> List[Tuple[str, Style]]:
    """把连续同色字符合并成 [(text, style)]（全屏渲染用）"""
    out: List[Tuple[str, Style]] = []
    for cell in line:
        if out and out[-1][1] == cell.style:
            out[-1] = (out[-1][0] + cell.ch, cell.style)
        else:
            out.append((cell.ch, cell.style))
    return out


def wrap_cells(line: Sequence[Cell], width: int) -> List[List[Cell]]:
    """按显示宽度折行（不拆宽字符；空行也返回一个空视觉行）"""
    if width <= 0:
        return [list(line)]
    out: List[List[Cell]] = []
    cur: List[Cell] = []
    cur_w = 0
    for cell in line:
        w = max(0, cell.width)
        if cur and cur_w + w > width:
            out.append(cur)
            cur = []
            cur_w = 0
        cur.append(cell)
        cur_w += w
    out.append(cur)
    return out


class _Line(list):
    """带版本号的逻辑行（v5.5.6 逐行折行缓存）。

    继承 list，所有原地变更操作自动递增 `_v`，供 `Screen._wrapped_line`
    判断"这一行自上次折行后是否变过"。未变过的行直接复用缓存的折行结果，
    把流式期间每帧 O(全部行) 的全量折行降为 O(变化行)。
    """

    __slots__ = ("_v",)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._v = 0

    def _bump(self):
        self._v += 1

    def append(self, x):
        super().append(x); self._bump()

    def extend(self, it):
        super().extend(it); self._bump()

    def insert(self, i, x):
        super().insert(i, x); self._bump()

    def pop(self, *a):
        r = super().pop(*a); self._bump(); return r

    def remove(self, x):
        super().remove(x); self._bump()

    def clear(self):
        super().clear(); self._bump()

    def __setitem__(self, i, v):
        super().__setitem__(i, v); self._bump()

    def __delitem__(self, i):
        super().__delitem__(i); self._bump()

    def __iadd__(self, it):
        super().__iadd__(it); self._bump(); return self

    def __imul__(self, n):
        super().__imul__(n); self._bump(); return self

    def sort(self, **kw):
        super().sort(**kw); self._bump()

    def reverse(self):
        super().reverse(); self._bump()


class Screen:
    """可增量喂入 ANSI 文本的逻辑屏幕"""

    def __init__(self, max_lines: int = MAX_SCROLLBACK_LINES,
                 on_commit=None):
        self._lines: List[List[Cell]] = [_Line()]
        # 逐行折行缓存（v5.5.6）：id(line) -> (line强引用, 行版本, 宽度, 折行结果)
        # 存强引用 + `is` 校验，杜绝 id 复用导致的假命中。
        self._wrap_cache: Dict[int, Tuple] = {}
        self._row: int = 0
        self._col: int = 0
        self._style: Style = DEFAULT_STYLE
        self.max_lines = max_lines
        # 内容版本号（每次 feed/append 递增；上层用于缓存折行结果）
        self._ver = 0
        # 行提交回调（transcript / S-11）：每次 `\n` 时把"刚写完的那一行"
        # 的纯文本交出去（不含 ANSI）。临时区域可用上层 flag 过滤。
        self._on_commit = on_commit

    # ------------------------------------------------------------------
    #  查询
    # ------------------------------------------------------------------
    @property
    def version(self) -> int:
        return self._ver

    @property
    def row(self) -> int:
        return self._row

    @property
    def col(self) -> int:
        return self._col

    @property
    def style(self) -> Style:
        return self._style

    def lines(self) -> List[List[Cell]]:
        """逻辑行（副本，按 `\\n` 切分；末尾可能有一个空行）"""
        return [list(line) for line in self._lines]

    def text(self) -> str:
        return "\n".join(_cells_text(line) for line in self._lines)

    def plain_lines(self) -> List[str]:
        return [_cells_text(line) for line in self._lines]

    def _wrapped_line(self, line: List[Cell], width: int) -> List[List[Tuple[str, Style]]]:
        """单行折行+样式合并（v5.5.6 逐行缓存：行版本/宽度未变则 O(1) 复用）"""
        ver = getattr(line, "_v", None)
        if ver is None:
            # 兜底：外部塞入的普通 list 无版本号，不做缓存（防陈旧命中）
            return [merge_runs(vline) for vline in wrap_cells(line, width)]
        key = id(line)
        ent = self._wrap_cache.get(key)
        if (ent is not None and ent[0] is line and ent[1] == ver
                and ent[2] == width):
            return ent[3]
        wrapped = [merge_runs(vline) for vline in wrap_cells(line, width)]
        self._wrap_cache[key] = (line, ver, width, wrapped)
        return wrapped

    def visual_lines(self, width: int) -> List[List[Tuple[str, Style]]]:
        """按给定宽度折行，返回每行的 [(文本, 样式)] 片段

        v5.5.6：逐行缓存——流式期间每帧只重折"变过的行"（通常只有末尾行），
        未变行 O(1) 复用；首次调用/宽度变化（resize）时才全量折行。
        """
        out: List[List[Tuple[str, Style]]] = []
        for line in self._lines:
            out.extend(self._wrapped_line(line, width))
        # 缓存剪枝：行被裁剪/替换后旧条目失效，防无界增长（摊还 O(N)，罕见）
        if len(self._wrap_cache) > len(self._lines) * 2 + 128:
            live = {id(l) for l in self._lines}
            self._wrap_cache = {k: v for k, v in self._wrap_cache.items()
                                if k in live}
        return out

    def visual_line_count(self, width: int) -> int:
        return sum(len(self._wrapped_line(line, width)) for line in self._lines)

    # ------------------------------------------------------------------
    #  喂入
    # ------------------------------------------------------------------
    def feed(self, text: str) -> None:
        self._ver += 1
        for ev in iter_csi(text):
            if ev[0] == "text":
                self._write_text(ev[1])
            else:
                _, params, final = ev
                self._handle_csi(params, final)

    def append_line(self, text: str, style: Style = DEFAULT_STYLE) -> None:
        """直接追加一整行（供测试/静态行使用）"""
        self._ver += 1
        old = self._style
        self._style = style
        self._write_text(text)
        self._newline()
        self._style = old

    # ------------------------------------------------------------------
    #  文本写入
    # ------------------------------------------------------------------
    def _ensure_line(self, row: int) -> List[Cell]:
        while len(self._lines) <= row:
            self._lines.append(_Line())
        return self._lines[row]

    def _write_text(self, text: str) -> None:
        for cluster in grapheme_clusters(text):
            if cluster == "\n":
                self._newline()
            elif cluster == "\r":
                self._col = 0
            elif cluster == "\b":
                self._col = max(0, self._col - 1)
            elif cluster == "\t":
                pad = 4 - (self._col % 4)
                for _ in range(pad):
                    self._put_cell(" ")
            elif cluster == "\x00":
                continue
            else:
                self._put_cell(cluster)

    def _current_line(self) -> List[Cell]:
        return self._ensure_line(self._row)

    def _put_cell(self, ch: str) -> None:
        w = display_width(ch)
        line = self._current_line()

        # 组合符（宽度 0）附着到前一个单元
        if w <= 0:
            if 0 < self._col <= len(line):
                prev = line[self._col - 1]
                line[self._col - 1] = Cell(prev.ch + ch, prev.style, prev.width)
            return

        while len(line) < self._col:
            line.append(Cell(" ", DEFAULT_STYLE, 1))

        cell = Cell(ch, self._style, w)
        if self._col < len(line):
            line[self._col] = cell
        else:
            line.append(cell)
        self._col += 1

    def _newline(self) -> None:
        if self._on_commit is not None:
            try:
                self._on_commit(_cells_text(self._lines[self._row]))
            except Exception:
                pass
        self._row += 1
        self._ensure_line(self._row)
        self._col = 0
        self._trim()

    def _trim(self) -> None:
        if self.max_lines > 0 and len(self._lines) > self.max_lines:
            drop = len(self._lines) - self.max_lines
            del self._lines[:drop]
            self._row = max(0, self._row - drop)

    # ------------------------------------------------------------------
    #  CSI
    # ------------------------------------------------------------------
    @staticmethod
    def _int_param(params: str, idx: int = 0, default: int = 1) -> int:
        if not params:
            return default
        parts = params.split(";")
        if idx >= len(parts):
            return default
        p = parts[idx].strip()
        if p == "":
            return default
        try:
            return int(p)
        except ValueError:
            return default

    def _handle_csi(self, params: str, final: str) -> None:
        if final == "m":
            self._style = parse_sgr(params, self._style)
            return

        if final == "K":  # 擦除行
            n = self._int_param(params, 0, 0)
            line = self._current_line()
            if n == 0:
                del line[self._col:]
            elif n == 1:
                for i in range(min(self._col + 1, len(line))):
                    line[i] = Cell(" ", DEFAULT_STYLE, 1)
            elif n == 2:
                line.clear()
            return

        if final == "J":  # 擦除屏幕
            n = self._int_param(params, 0, 0)
            if n == 0:
                line = self._current_line()
                del line[self._col:]
                del self._lines[self._row + 1:]
            elif n == 1:
                for r in range(self._row):
                    self._lines[r] = _Line()
                line = self._current_line()
                for i in range(min(self._col + 1, len(line))):
                    line[i] = Cell(" ", DEFAULT_STYLE, 1)
            elif n == 2:
                self._lines = [_Line() for _ in self._lines]
            return

        if final == "A":  # 上移
            self._row = max(0, self._row - self._int_param(params, 0, 1))
            return

        if final == "B":  # 下移
            self._row += self._int_param(params, 0, 1)
            self._ensure_line(self._row)
            return

        if final == "C":  # 右移
            self._col += self._int_param(params, 0, 1)
            return

        if final == "D":  # 左移
            self._col = max(0, self._col - self._int_param(params, 0, 1))
            return

        if final in ("H", "f"):  # 绝对定位（1-based）
            row = self._int_param(params, 0, 1) - 1
            col = self._int_param(params, 1, 1) - 1
            self._row = max(0, row)
            self._col = max(0, col)
            self._ensure_line(self._row)
            return

        # 其它 CSI（如 ?25h 光标显示、?1049h 备用屏）由上层处理，这里忽略
        return
