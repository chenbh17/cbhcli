"""ANSI / SGR 解析与编码（P2）

契约位置：`docs/全屏重构行为契约.md`
  - T-05  256 色：`ColorDepth.DEPTH_8_BIT`，hex 再编码回 256 色码
  - T-06  16 色与属性（bold/dim/italic/underline/reverse/strike/blink）
  - H-05  颜色解析不依赖 rich/prompt_toolkit，避免渲染层耦合

设计要点：
  1. `Style` 是**不可变**的（frozen dataclass），可哈希，便于按样式合并文本片段；
  2. 颜色用三种表示：("ansi", 0..15) / ("256", 0..255) / ("rgb", (r,g,b))；
  3. 自带**标准 xterm 256 色表**（不依赖 prompt_toolkit 的私有表——实测其表只有
     254 项，索引 254/255 缺失，直接用会丢色）；
  4. `style_to_pt_style()` 输出 prompt_toolkit 样式串（供全屏渲染器使用）：
     16 色用 `ansired` 等名字（与现状观感一致），256/truecolor 用 `#rrggbb`
     （配合 `ColorDepth.DEPTH_8_BIT` 会回编码成 256 色码，与旧输出等价）；
  5. `style_to_sgr()` 用于回写/对比/transcript，语义等价即可（不追求逐字节）。
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Iterator, List, Optional, Tuple

__all__ = [
    "ANSI16_RGB",
    "PALETTE256",
    "Style",
    "DEFAULT_STYLE",
    "palette_rgb",
    "parse_sgr",
    "style_to_pt_style",
    "style_to_sgr",
    "iter_csi",
    "ansi_to_segments",
    "segments_to_ansi",
]

# ----------------------------------------------------------------------
#  调色板（标准 xterm 256）
# ----------------------------------------------------------------------
ANSI16_RGB: Tuple[Tuple[int, int, int], ...] = (
    (0, 0, 0), (205, 0, 0), (0, 205, 0), (205, 205, 0),
    (0, 0, 238), (205, 0, 205), (0, 205, 205), (229, 229, 229),
    (127, 127, 127), (255, 0, 0), (0, 255, 0), (255, 255, 0),
    (92, 92, 255), (255, 0, 255), (0, 255, 255), (255, 255, 255),
)

# prompt_toolkit / xterm 的 16 色名字（顺序与 ANSI16_RGB 一致）
ANSI16_NAMES: Tuple[str, ...] = (
    "ansiblack", "ansired", "ansigreen", "ansiyellow",
    "ansiblue", "ansimagenta", "ansicyan", "ansigray",
    "ansibrightblack", "ansibrightred", "ansibrightgreen", "ansibrightyellow",
    "ansibrightblue", "ansibrightmagenta", "ansibrightcyan", "ansiwhite",
)


def _build_palette() -> Tuple[Tuple[int, int, int], ...]:
    cols: List[Tuple[int, int, int]] = list(ANSI16_RGB)
    levels = (0, 95, 135, 175, 215, 255)
    for r in levels:
        for g in levels:
            for b in levels:
                cols.append((r, g, b))
    for i in range(24):
        v = 8 + i * 10
        cols.append((v, v, v))
    assert len(cols) == 256
    return tuple(cols)


PALETTE256 = _build_palette()


def palette_rgb(n: int) -> Tuple[int, int, int]:
    """xterm 256 色索引 → RGB（越界按 0 处理）"""
    if 0 <= n < 256:
        return PALETTE256[n]
    return (0, 0, 0)


# ----------------------------------------------------------------------
#  样式
# ----------------------------------------------------------------------
Color = Tuple  # ("ansi", n) | ("256", n) | ("rgb", (r, g, b))


@dataclass(frozen=True)
class Style:
    """终端样式（不可变、可哈希）"""

    fg: Optional[Color] = None
    bg: Optional[Color] = None
    bold: bool = False
    dim: bool = False
    italic: bool = False
    underline: bool = False
    reverse: bool = False
    strike: bool = False
    blink: bool = False

    def is_default(self) -> bool:
        return self == DEFAULT_STYLE


DEFAULT_STYLE = Style()


# ----------------------------------------------------------------------
#  SGR 解析
# ----------------------------------------------------------------------
def _parse_params(params: str) -> List[int]:
    if params is None or params == "":
        return [0]
    out: List[int] = []
    for p in params.split(";"):
        p = p.strip()
        if p == "":
            out.append(0)
        elif p.startswith("?"):
            continue
        else:
            try:
                out.append(int(p))
            except ValueError:
                out.append(0)
    return out


def parse_sgr(params: str, style: Style = DEFAULT_STYLE) -> Style:
    """解析 SGR 参数并在给定样式上增量应用（返回新对象）"""
    parts = _parse_params(params)
    i = 0
    while i < len(parts):
        p = parts[i]
        if p == 0:
            style = DEFAULT_STYLE
        elif p == 1:
            style = replace(style, bold=True)
        elif p == 2:
            style = replace(style, dim=True)
        elif p == 3:
            style = replace(style, italic=True)
        elif p == 4:
            style = replace(style, underline=True)
        elif p in (5, 6):
            style = replace(style, blink=True)
        elif p == 7:
            style = replace(style, reverse=True)
        elif p == 9:
            style = replace(style, strike=True)
        elif p == 22:
            style = replace(style, bold=False, dim=False)
        elif p == 23:
            style = replace(style, italic=False)
        elif p == 24:
            style = replace(style, underline=False)
        elif p == 25:
            style = replace(style, blink=False)
        elif p == 27:
            style = replace(style, reverse=False)
        elif p == 29:
            style = replace(style, strike=False)
        elif p in (38, 48):
            color: Optional[Color] = None
            if i + 1 < len(parts) and parts[i + 1] == 5 and i + 2 < len(parts):
                n = parts[i + 2]
                # 0~15 是"终端 ANSI 调色板"，必须保留为 ansi 名，
                # 否则按 hex 输出会被 prompt_toolkit 量化到 6x6x6 立方体
                # （实测 ansired #cd0000 → 160，色差可见）。
                color = ("ansi", n) if 0 <= n < 16 else ("256", n)
                i += 2
            elif i + 1 < len(parts) and parts[i + 1] == 2 and i + 4 < len(parts):
                color = ("rgb", (parts[i + 2], parts[i + 3], parts[i + 4]))
                i += 4
            style = replace(style, fg=color) if p == 38 else replace(style, bg=color)
        elif 30 <= p <= 37:
            style = replace(style, fg=("ansi", p - 30))
        elif 90 <= p <= 97:
            style = replace(style, fg=("ansi", 8 + (p - 90)))
        elif p == 39:
            style = replace(style, fg=None)
        elif 40 <= p <= 47:
            style = replace(style, bg=("ansi", p - 40))
        elif 100 <= p <= 107:
            style = replace(style, bg=("ansi", 8 + (p - 100)))
        elif p == 49:
            style = replace(style, bg=None)
        i += 1
    return style


# ----------------------------------------------------------------------
#  编码
# ----------------------------------------------------------------------
def _color_to_pt(color: Color) -> str:
    kind = color[0]
    if kind == "ansi":
        n = int(color[1])
        return ANSI16_NAMES[n] if 0 <= n < 16 else "ansidefault"
    if kind == "256":
        r, g, b = palette_rgb(int(color[1]))
        return f"#{r:02x}{g:02x}{b:02x}"
    _, (r, g, b) = color
    return f"#{int(r):02x}{int(g):02x}{int(b):02x}"


def style_to_pt_style(style: Style) -> str:
    """转成 prompt_toolkit 样式串（供全屏渲染器/单测使用）"""
    parts: List[str] = []
    if style.fg is not None:
        parts.append("fg:" + _color_to_pt(style.fg))
    if style.bg is not None:
        parts.append("bg:" + _color_to_pt(style.bg))
    for flag, name in (
        ("bold", "bold"), ("dim", "dim"), ("italic", "italic"),
        ("underline", "underline"), ("reverse", "reverse"),
        ("strike", "strike"), ("blink", "blink"),
    ):
        if getattr(style, flag):
            parts.append(name)
    return " ".join(parts)


def _color_to_sgr(color: Color, is_bg: bool = False) -> str:
    kind = color[0]
    if kind == "ansi":
        n = int(color[1])
        if 0 <= n < 8:
            return str((40 if is_bg else 30) + n)
        if 8 <= n < 16:
            return str((100 if is_bg else 90) + (n - 8))
        return str(49 if is_bg else 39)
    if kind == "256":
        return f"{48 if is_bg else 38};5;{int(color[1])}"
    _, (r, g, b) = color
    return f"{48 if is_bg else 38};2;{int(r)};{int(g)};{int(b)}"


def style_to_sgr(style: Style) -> str:
    """样式 → SGR 序列（总是先 reset，语义等价即可）"""
    if style.is_default():
        return "\x1b[0m"
    codes: List[str] = ["0"]
    if style.bold:
        codes.append("1")
    if style.dim:
        codes.append("2")
    if style.italic:
        codes.append("3")
    if style.underline:
        codes.append("4")
    if style.blink:
        codes.append("5")
    if style.reverse:
        codes.append("7")
    if style.strike:
        codes.append("9")
    if style.fg is not None:
        codes.append(_color_to_sgr(style.fg, is_bg=False))
    if style.bg is not None:
        codes.append(_color_to_sgr(style.bg, is_bg=True))
    return "\x1b[" + ";".join(codes) + "m"


# ----------------------------------------------------------------------
#  流式解析
# ----------------------------------------------------------------------
def iter_csi(text: str) -> Iterator[Tuple]:
    """把文本切成 ("text", str) / ("csi", params, final) 事件

    - 处理 CSI：`ESC [ ... <final>`（final 在 0x40~0x7E）
    - 处理 OSC：`ESC ] ... BEL` 或 `ESC \\`（直接丢弃，不进入文本）
    - 其它 ESC 序列：丢弃 ESC 与下一个字符
    """
    i = 0
    buf: List[str] = []
    n = len(text)
    while i < n:
        ch = text[i]
        if ch == "\x1b":
            if buf:
                yield ("text", "".join(buf))
                buf = []
            if i + 1 >= n:
                break
            nxt = text[i + 1]
            if nxt == "[":
                j = i + 2
                params = []
                while j < n and not ("@" <= text[j] <= "~"):
                    params.append(text[j])
                    j += 1
                if j < n:
                    yield ("csi", "".join(params), text[j])
                    i = j + 1
                else:
                    break
            elif nxt == "]":
                j = i + 2
                while j < n:
                    if text[j] == "\x07":
                        j += 1
                        break
                    if text[j] == "\x1b" and j + 1 < n and text[j + 1] == "\\":
                        j += 2
                        break
                    j += 1
                i = j
            else:
                i += 2
            continue
        buf.append(ch)
        i += 1
    if buf:
        yield ("text", "".join(buf))


def ansi_to_segments(text: str) -> List[Tuple[str, Style]]:
    """ANSI 文本 → [(文本片段, 样式)]（只消费 SGR，其它 CSI 忽略）"""
    style = DEFAULT_STYLE
    out: List[Tuple[str, Style]] = []
    for ev in iter_csi(text):
        if ev[0] == "text":
            if ev[1]:
                out.append((ev[1], style))
        else:
            _, params, final = ev
            if final == "m":
                style = parse_sgr(params, style)
    return out


def segments_to_ansi(segments: List[Tuple[str, Style]]) -> str:
    """[(文本片段, 样式)] → ANSI 文本"""
    out: List[str] = []
    last: Optional[Style] = None
    for text, style in segments:
        if not text:
            continue
        if style != last:
            out.append(style_to_sgr(style))
            last = style
        out.append(text)
    if last is not None:
        out.append("\x1b[0m")
    return "".join(out)
