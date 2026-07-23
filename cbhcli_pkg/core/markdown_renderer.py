"""Markdown 流式渲染器

采用「流式纯文本 + 完成后清除重新渲染」策略：
1. feed() 时用 sys.stdout.write 逐字符输出纯文本（保留打字感）
2. flush() 时用 ANSI 控制码清除纯文本，再用 Rich Markdown 重新渲染

行数计算包含可选的前缀（如 "AI: "），确保清除准确。

LaTeX 公式支持：
- 块级公式 $$...$$ → 转换为 Unicode，用引用块显示
- 行内公式 $...$ → 转换为 Unicode，内联显示
- 支持希腊字母、上下标、分数、根号、求和、积分等常见 LaTeX 命令

支持的 Markdown 元素：
- 标题 (H1-H6)、加粗、斜体、删除线、行内代码
- 代码块（语法高亮）、无序/有序列表、嵌套列表
- 表格、引用块、水平线、链接、图片
"""

import os
import re
import sys
import unicodedata
from typing import Optional

from rich.console import Console
from rich.markdown import Markdown, Heading
from rich.theme import Theme


# 修改 Rich Heading 的默认对齐：所有标题左对齐（原 H1 居中）
Heading.LEVEL_ALIGN = {
    "h1": "left",
    "h2": "left",
    "h3": "left",
    "h4": "left",
    "h5": "left",
    "h6": "left",
}


# Markdown 渲染主题
_MARKDOWN_THEME = Theme({
    # 标题 — 白色加粗
    "markdown.h1": "bold white",
    "markdown.h2": "bold white",
    "markdown.h3": "bold white",
    "markdown.h4": "bold white",
    "markdown.h5": "bold white",
    "markdown.h6": "bold white",
    # 行内格式 — 加粗用白色
    "markdown.strong": "bold white",
    "markdown.em": "italic",
    "markdown.code": "bold cyan on #2a2a2a",
    "markdown.s": "strike",
    # 链接
    "markdown.link": "blue underline",
    "markdown.link_url": "dim blue",
    # 其他元素
    "markdown.kbd": "bold",
    "markdown.blockquote": "italic",
    "markdown.blockquote.par": "italic",
    "markdown.hr": "dim",
    "markdown.code_block": "on #1a1a1a",
    # 表格
    "markdown.table.head": "bold cyan",
    "markdown.table.row": "",
    # 列表 — 白色
    "markdown.item.bullet": "white",
    "markdown.item.number": "white",
    # 段落
    "markdown.par": "",
    "markdown.text": "",
})


# ==================================================================
#  LaTeX → Unicode 转换
# ==================================================================

# LaTeX 命令 → Unicode 符号映射表
_LATEX_SYMBOLS = {
    # 基本运算
    r'\times': '×', r'\div': '÷', r'\pm': '±', r'\mp': '∓',
    r'\cdot': '·', r'\cdots': '⋯', r'\ldots': '…', r'\vdots': '⋮', r'\ddots': '⋱',
    # 关系符号
    r'\geq': '≥', r'\ge': '≥', r'\leq': '≤', r'\le': '≤',
    r'\neq': '≠', r'\ne': '≠', r'\approx': '≈', r'\equiv': '≡',
    r'\sim': '∼', r'\propto': '∝', r'\ll': '≪', r'\gg': '≫',
    # 箭头
    r'\rightarrow': '→', r'\to': '→', r'\leftarrow': '←', r'\gets': '←',
    r'\Rightarrow': '⇒', r'\implies': '⇒', r'\Leftarrow': '⇐',
    r'\Leftrightarrow': '⇔', r'\iff': '⇔', r'\leftrightarrow': '↔',
    r'\mapsto': '↦', r'\uparrow': '↑', r'\downarrow': '↓',
    # 集合
    r'\in': '∈', r'\notin': '∉', r'\ni': '∋',
    r'\subset': '⊂', r'\supset': '⊃', r'\subseteq': '⊆', r'\supseteq': '⊇',
    r'\cup': '∪', r'\cap': '∩', r'\emptyset': '∅', r'\varnothing': '∅',
    r'\setminus': '∖', r'\complement': '∁',
    # 逻辑
    r'\forall': '∀', r'\exists': '∃', r'\nexists': '∄',
    r'\neg': '¬', r'\land': '∧', r'\lor': '∨', r'\lnot': '¬',
    # 希腊字母（小写）
    r'\alpha': 'α', r'\beta': 'β', r'\gamma': 'γ', r'\delta': 'δ',
    r'\epsilon': 'ε', r'\varepsilon': 'ε', r'\zeta': 'ζ', r'\eta': 'η',
    r'\theta': 'θ', r'\vartheta': 'ϑ', r'\iota': 'ι', r'\kappa': 'κ',
    r'\lambda': 'λ', r'\mu': 'μ', r'\nu': 'ν', r'\xi': 'ξ',
    r'\pi': 'π', r'\varpi': 'ϖ', r'\rho': 'ρ', r'\varrho': 'ϱ',
    r'\sigma': 'σ', r'\varsigma': 'ς', r'\tau': 'τ', r'\upsilon': 'υ',
    r'\phi': 'φ', r'\varphi': 'φ', r'\chi': 'χ', r'\psi': 'ψ', r'\omega': 'ω',
    # 希腊字母（大写）
    r'\Gamma': 'Γ', r'\Delta': 'Δ', r'\Theta': 'Θ', r'\Lambda': 'Λ',
    r'\Xi': 'Ξ', r'\Pi': 'Π', r'\Sigma': 'Σ', r'\Upsilon': 'Υ',
    r'\Phi': 'Φ', r'\Psi': 'Ψ', r'\Omega': 'Ω',
    # 微积分
    r'\infty': '∞', r'\partial': '∂', r'\nabla': '∇',
    r'\int': '∫', r'\iint': '∬', r'\iiint': '∭', r'\oint': '∮',
    r'\sum': 'Σ', r'\prod': '∏', r'\coprod': '∐',
    # 括号
    r'\langle': '⟨', r'\rangle': '⟩', r'\lceil': '⌈', r'\rceil': '⌉',
    r'\lfloor': '⌊', r'\rfloor': '⌋',
    # 其他
    r'\sqrt': '√', r'\angle': '∠', r'\perp': '⊥', r'\parallel': '∥',
    r'\degree': '°', r'\circ': '∘', r'\bullet': '•', r'\star': '⋆',
    r'\dagger': '†', r'\ddagger': '‡', r'\prime': '′',
    r'\backslash': '\\', r'\%': '%', r'\#': '#', r'\&': '&',
    r'\_': '_', r'\$': '$',
    # 函数名（保持普通文本）
    r'\sin': 'sin', r'\cos': 'cos', r'\tan': 'tan', r'\cot': 'cot',
    r'\sec': 'sec', r'\csc': 'csc', r'\arcsin': 'arcsin', r'\arccos': 'arccos',
    r'\arctan': 'arctan', r'\sinh': 'sinh', r'\cosh': 'cosh', r'\tanh': 'tanh',
    r'\log': 'log', r'\ln': 'ln', r'\exp': 'exp', r'\lim': 'lim',
    r'\max': 'max', r'\min': 'min', r'\sup': 'sup', r'\inf': 'inf',
    r'\det': 'det', r'\dim': 'dim', r'\ker': 'ker', r'\deg': 'deg',
    r'\gcd': 'gcd', r'\hom': 'hom', r'\arg': 'arg',
    # \dfrac → \frac（后面统一处理）
    r'\dfrac': r'\frac',
    # \tfrac → \frac
    r'\tfrac': r'\frac',
}

# Unicode 上标字符映射
_SUPERSCRIPT = {
    '0': '⁰', '1': '¹', '2': '²', '3': '³', '4': '⁴',
    '5': '⁵', '6': '⁶', '7': '⁷', '8': '⁸', '9': '⁹',
    '+': '⁺', '-': '⁻', '=': '⁼', '(': '⁽', ')': '⁾',
    'n': 'ⁿ', 'i': 'ⁱ', 'x': 'ˣ', 'a': 'ᵃ', 'b': 'ᵇ', 'c': 'ᶜ',
    'd': 'ᵈ', 'e': 'ᵉ', 'f': 'ᶠ', 'g': 'ᵍ', 'h': 'ʰ', 'j': 'ʲ',
    'k': 'ᵏ', 'l': 'ˡ', 'm': 'ᵐ', 'o': 'ᵒ', 'p': 'ᵖ', 'r': 'ʳ',
    's': 'ˢ', 't': 'ᵗ', 'u': 'ᵘ', 'v': 'ᵛ', 'w': 'ʷ',
    'α': 'ᵅ', 'β': 'ᵝ', 'γ': 'ᵞ', 'δ': 'ᵟ', 'ε': 'ᵋ',
    'θ': 'ᶿ', 'ι': 'ᶥ', 'φ': 'ᵠ', 'χ': 'ᵡ', 'ψ': 'ᵠ',
    'λ': 'ᶫ', 'μ': 'ᵘ', 'ν': 'ᵛ', 'σ': 'ˢ',
    '∂': '∂', '∞': '∞',
}

# Unicode 下标字符映射
_SUBSCRIPT = {
    '0': '₀', '1': '₁', '2': '₂', '3': '₃', '4': '₄',
    '5': '₅', '6': '₆', '7': '₇', '8': '₈', '9': '₉',
    '+': '₊', '-': '₋', '=': '₌', '(': '₍', ')': '₎',
    'a': 'ₐ', 'e': 'ₑ', 'h': 'ₕ', 'i': 'ᵢ', 'j': 'ⱼ',
    'k': 'ₖ', 'l': 'ₗ', 'm': 'ₘ', 'n': 'ₙ', 'o': 'ₒ',
    'p': 'ₚ', 'r': 'ᵣ', 's': 'ₛ', 't': 'ₜ', 'u': 'ᵤ',
    'v': 'ᵥ', 'x': 'ₓ',
    'α': 'ᵅ', 'β': 'ᵦ', 'γ': 'ᵧ', 'δ': 'ᵨ', 'ε': 'ᵩ',
    'θ': 'ᵩ', 'φ': 'ᵩ', 'χ': 'ᵪ', 'ρ': 'ᵨ',
}


def _latex_to_unicode(latex_str: str) -> str:
    """将 LaTeX 公式转换为 Unicode 文本

    支持的 LaTeX 命令：
    - 希腊字母、运算符、关系符号、箭头、集合等
    - 上下标 x^2, x_1, x^{ab}, x_{ab}
    - 分数 \\frac{a}{b} → (a)/(b)
    - 开方 \\sqrt{x} → √(x), \\sqrt[n]{x} → ⁿ√(x)
    - 文本 \\text{...}, \\mathrm{...}
    - 黑板粗体 \\mathbb{R} → ℝ
    - 装饰 \\boxed{x} → [x], \\overline{x} → x̄
    """
    s = latex_str

    # 1. 先替换所有 LaTeX 命令（希腊字母、符号等，\dfrac→\frac）
    for cmd in sorted(_LATEX_SYMBOLS.keys(), key=len, reverse=True):
        s = s.replace(cmd, _LATEX_SYMBOLS[cmd])

    # 2. 处理 \text{...}, \mathrm{...} 等
    s = re.sub(r'\\text\{([^}]*)\}', r'\1', s)
    s = re.sub(r'\\mathrm\{([^}]*)\}', r'\1', s)
    s = re.sub(r'\\mathit\{([^}]*)\}', r'\1', s)
    s = re.sub(r'\\mathbf\{([^}]*)\}', r'\1', s)
    s = re.sub(r'\\operatorname\{([^}]*)\}', r'\1', s)

    # 3. 处理 \mathbb{...}
    s = re.sub(r'\\mathbb\{([^}]*)\}', lambda m: {
        'R': 'ℝ', 'N': 'ℕ', 'Z': 'ℤ', 'Q': 'ℚ', 'C': 'ℂ',
        'H': 'ℍ', 'O': '𝕆', '1': '𝟙', 'P': 'ℙ'
    }.get(m.group(1), m.group(1)), s)

    # 4. 处理 \frac{a}{b} → (a)/(b)
    def _replace_frac(text):
        result = text
        changed = True
        while changed:
            changed = False
            pattern = r'\\frac\{([^{}]*)\}\{([^{}]*)\}'
            new = re.sub(pattern, r'(\1)/(\2)', result)
            if new != result:
                result = new
                changed = True
        return result
    s = _replace_frac(s)

    # 5. 处理 \boxed{...} → [...]
    def _replace_boxed(text):
        result = text
        changed = True
        while changed:
            changed = False
            pattern = r'\\boxed\{([^{}]*)\}'
            new = re.sub(pattern, r'[\1]', result)
            if new != result:
                result = new
                changed = True
        return result
    s = _replace_boxed(s)

    # 6. 处理 \overline{...} → ...̄
    s = re.sub(r'\\overline\{([^{}]*)\}', lambda m: m.group(1) + '\u0305', s)

    # 7. 处理 \sqrt[n]{x} → ⁿ√(x)
    def _replace_sqrt_n(text):
        result = text
        changed = True
        while changed:
            changed = False
            pattern = r'\\sqrt\[([^\]]*)\]\{([^{}]*)\}'
            new = re.sub(pattern, lambda m: m.group(1) + '√(' + m.group(2) + ')', result)
            if new != result:
                result = new
                changed = True
        return result
    s = _replace_sqrt_n(s)

    # 8. 处理 \sqrt{x} → √(x)
    def _replace_sqrt(text):
        result = text
        changed = True
        while changed:
            changed = False
            pattern = r'\\sqrt\{([^{}]*)\}'
            new = re.sub(pattern, r'√(\1)', result)
            if new != result:
                result = new
                changed = True
        return result
    s = _replace_sqrt(s)

    # 9. 处理上标 x^{...} 和 x^单字符
    def _replace_superscript(text):
        result = text
        changed = True
        while changed:
            changed = False
            # x^{...}
            pattern = r'\^\{([^{}]*)\}'
            def _sub_sup(m):
                content = m.group(1)
                return ''.join(_SUPERSCRIPT.get(c, c) for c in content)
            new = re.sub(pattern, _sub_sup, result)
            if new != result:
                result = new
                changed = True
                continue
            # x^单字符
            pattern = r'\^([a-zA-Z0-9])'
            new = re.sub(pattern, lambda m: _SUPERSCRIPT.get(m.group(1), '^' + m.group(1)), result)
            if new != result:
                result = new
                changed = True
        return result
    s = _replace_superscript(s)

    # 10. 处理下标 x_{...} 和 x_单字符
    def _replace_subscript(text):
        result = text
        changed = True
        while changed:
            changed = False
            # x_{...}
            pattern = r'_\{([^{}]*)\}'
            def _sub_sub(m):
                content = m.group(1)
                return ''.join(_SUBSCRIPT.get(c, c) for c in content)
            new = re.sub(pattern, _sub_sub, result)
            if new != result:
                result = new
                changed = True
                continue
            # x_单字符
            pattern = r'_([a-zA-Z0-9])'
            new = re.sub(pattern, lambda m: _SUBSCRIPT.get(m.group(1), '_' + m.group(1)), result)
            if new != result:
                result = new
                changed = True
        return result
    s = _replace_subscript(s)

    # 11. 清理残留的花括号和未匹配的 \xxx 命令
    s = s.replace('{', '').replace('}', '')
    s = re.sub(r'\\[a-zA-Z]+', '', s)

    # 12. 清理多余空格
    s = re.sub(r'  +', ' ', s)

    return s.strip()


def _preprocess_latex(text: str) -> str:
    """预处理文本中的 LaTeX 公式，转换为 Unicode

    块级公式 $$...$$ → 转换为 Unicode，用引用块 > 显示
    行内公式 $...$ → 转换为 Unicode，内联显示
    """
    # 1. 处理块级公式 $$...$$ → 引用块
    def _replace_block(m):
        formula = m.group(1).strip()
        converted = _latex_to_unicode(formula)
        return '\n\n> ' + converted + '\n\n'
    text = re.sub(r'\$\$([^$]+)\$\$', _replace_block, text)

    # 2. 处理行内公式 $...$
    def _replace_inline(m):
        formula = m.group(1).strip()
        converted = _latex_to_unicode(formula)
        return converted
    text = re.sub(r'\$([^$]+)\$', _replace_inline, text)

    return text


# ==================================================================
#  终端行数计算
# ==================================================================

def _display_width(text: str) -> int:
    """计算字符串的终端显示宽度（中文/全角/Ambiguous=2，其他=1）

    ANSI 转义码不计入宽度。
    Ambiguous 字符（如 ─│→≥απΣ∫ 等）在中文终端中宽度为 2。
    """
    width = 0
    in_escape = False
    for ch in text:
        if ch == '\033':
            in_escape = True
            continue
        if in_escape:
            if ch.isalpha():
                in_escape = False
            continue
        if ch == '\t':
            width = (width + 4) & ~3
        elif unicodedata.east_asian_width(ch) in ('W', 'F', 'A'):
            width += 2
        else:
            width += 1
    return width


def _count_terminal_lines(text: str, term_width: int) -> int:
    """计算文本在终端中占用的行数（考虑自动换行）

    ANSI 转义码不计入宽度。

    Args:
        text: 文本内容
        term_width: 终端宽度（列数）

    Returns:
        占用的终端行数
    """
    if not text:
        return 0
    lines = 0
    for line in text.split('\n'):
        w = _display_width(line)
        if w == 0:
            lines += 1
        else:
            lines += max(1, (w + term_width - 1) // term_width)
    return lines


# ==================================================================
#  MarkdownStreamRenderer
# ==================================================================

class MarkdownStreamRenderer:
    """Markdown 流式渲染器

    采用「逐段落流式渲染」策略：
    - feed() 时累积内容，检测已完成的段落（空行分隔），逐段落用 Rich Markdown 渲染
    - flush() 时渲染剩余的未完成内容
    - 代码块内部不按空行分割，保持完整

    LaTeX 支持：渲染前自动将 $...$ 和 $$...$$ 转换为 Unicode。

    用法:
        renderer = MarkdownStreamRenderer()
        renderer.set_prefix("\\033[37mAI: \\033[0m")
        for chunk in stream:
            raw = renderer.feed(chunk)
            ai_response += raw
        renderer.flush()
    """

    def __init__(self, code_theme: str = "monokai"):
        """初始化

        Args:
            code_theme: 代码块语法高亮主题（Pygments 主题名）
        """
        self.console = Console(theme=_MARKDOWN_THEME)
        self.code_theme = code_theme
        self._buffer = ""           # 累积的原始文本（不含前缀）
        self._started = False       # 是否已启动
        self._prefix = ""           # 前缀（如 "AI: "，可含 ANSI 码）
        self._prefix_printed = False  # 前缀是否已输出

    @property
    def started(self) -> bool:
        """是否已启动"""
        return self._started

    def set_prefix(self, prefix: str):
        """设置前缀（如 "AI: "，可含 ANSI 颜色码）

        前缀会在首次渲染段落时输出到终端。
        """
        self._prefix = prefix

    def start(self):
        """启动流式渲染"""
        if self._started:
            return
        self._started = True
        self._buffer = ""
        self._prefix_printed = False

    @staticmethod
    def _line_type(line: str) -> str:
        """判断 Markdown 行类型"""
        s = line.strip()
        if not s:
            return 'empty'
        if s.startswith('```'):
            return 'code_fence'
        if s.startswith('|'):
            return 'table'
        if s.startswith('#'):
            return 'heading'
        if s.startswith('- ') or s.startswith('* ') or re.match(r'^\d+\. ', s):
            return 'list'
        if s.startswith('>'):
            return 'quote'
        if s.startswith('$$'):
            return 'math_block'
        return 'text'

    @classmethod
    def _find_safe_split_point(cls, text: str) -> int:
        """找到最后一个安全分割点

        分割规则：
        - 只在双换行符（\\n\\n，即段落分隔符）处分割
        - 代码块（```...```）内部不分割

        返回分割点位置（在该位置之前的内容可以安全渲染）。
        """
        in_code_block = False
        last_safe = 0

        # 逐行扫描，检测代码块状态和双换行符
        lines = text.split('\n')
        pos = 0

        for i, line in enumerate(lines):
            stripped = line.strip()

            if stripped.startswith('```'):
                in_code_block = not in_code_block
                pos += len(line) + 1
                continue

            if in_code_block:
                pos += len(line) + 1
                continue

            # 检测双换行符：当前行是空行，且不是最后一行
            # split('\n') 的最后一个元素可能是空字符串（由末尾 \n 产生）
            # 这不是真正的空行，只有 \n\n 中的空行才是段落分隔符
            if stripped == '' and i < len(lines) - 1:
                # 双换行符位置 = 当前 pos + 空行长度 + \n
                last_safe = pos + len(line) + 1

            pos += len(line) + 1

        return last_safe

    def _render_paragraphs(self, text: str):
        """将文本用 Rich Markdown 渲染并输出

        首次渲染时输出前缀。
        """
        if not text.strip():
            return

        # 输出前缀（仅首次）
        if not self._prefix_printed:
            if self._prefix:
                sys.stdout.write(self._prefix)
                sys.stdout.flush()
            self._prefix_printed = True

        # 预处理 LaTeX
        render_text = _preprocess_latex(text)

        # 用 Rich Markdown 渲染
        self.console.print(
            Markdown(render_text, code_theme=self.code_theme)
        )

    def feed(self, content: str) -> str:
        """接收流式内容，逐段落渲染已完成的部分

        首次调用时自动启动渲染器。
        已完成的段落（空行分隔，代码块内部不分割）会立即渲染输出。

        Args:
            content: 流式接收的一个 chunk

        Returns:
            原始内容（用于 ai_response 累积）
        """
        # 自动启动
        if not self._started:
            self.start()

        self._buffer += content

        # 找到安全分割点
        split_pos = self._find_safe_split_point(self._buffer)

        if split_pos > 0:
            # 渲染已完成部分
            completed = self._buffer[:split_pos]
            self._buffer = self._buffer[split_pos:]
            self._render_paragraphs(completed)

        return content

    def flush(self) -> str:
        """结束流式渲染，渲染剩余的未完成内容

        Returns:
            空字符串（所有内容已通过 feed() 返回）
        """
        if not self._started:
            return ""

        self._started = False

        if not self._buffer.strip():
            return ""

        # 渲染剩余内容
        self._render_paragraphs(self._buffer)
        self._buffer = ""

        return ""

    def cleanup(self):
        """强制清理终端状态（用于 Ctrl+C 中断等异常场景）

        不清除已输出的纯文本（保留用户已看到的内容）。
        """
        self._started = False
        # 确保光标可见
        sys.stdout.write("\033[?25h")
        sys.stdout.flush()