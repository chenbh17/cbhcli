"""文件编辑工具 - 精确字符串替换（空白/Unicode转义宽容匹配兜底）"""
import re
from pathlib import Path
from cbhcli_pkg.tools.registry import BaseTool, ToolResult

# 常见空白字符：普通空格、非断行空格(U+00A0)、数字空格(U+2007)、窄非断行空格(U+202F)
_WS_CHARS = (" ", chr(0xA0), chr(0x2007), chr(0x202F))
_WS_CLASS = "[" + " " + chr(0xA0) + chr(0x2007) + chr(0x202F) + "]"


def _find_exact_spans(content: str, old_str: str) -> list[tuple[int, int]]:
    """精确匹配，返回 (start, end) 区间列表"""
    spans = []
    start = 0
    while True:
        pos = content.find(old_str, start)
        if pos == -1:
            break
        spans.append((pos, pos + len(old_str)))
        start = pos + 1
    return spans


def _find_flex_spans(content: str, old_str: str) -> list[tuple[int, int]]:
    """空白字符宽容匹配：old_str 中的空格类字符可匹配文件中任意等价空白。

    解决文件含非断行空格(U+00A0)而 old_str 用普通空格（或反之）时
    精确匹配失败、报"未找到匹配的文本"的问题。
    """
    # 仅在 old_str 含空白字符时才有意义
    if not any(ch in _WS_CHARS for ch in old_str):
        return []
    parts = []
    for ch in old_str:
        if ch in _WS_CHARS:
            parts.append(_WS_CLASS)
        else:
            parts.append(re.escape(ch))
    try:
        pattern = re.compile("".join(parts))
    except re.error:
        return []
    return [(m.start(), m.end()) for m in pattern.finditer(content)]


def _escape_to_char(s: str) -> str:
    """把字面转义序列（反斜杠 + u + 4位十六进制）还原为实际字符。

    解决 LLM 工具调用参数经 JSON 编解码后，old_str 中
    Unicode 转义序列转义层级变化、与文件内容不一致的问题。
    """
    BS = chr(92)  # 反斜杠字符（运行时构造，避免源码转义歧义）
    # 正则中匹配字面反斜杠需要双反斜杠（BS+BS → 匹配 1 个字面 \）
    pattern = BS + BS + "u([0-9a-fA-F]{4})"

    def repl(m):
        code = m.group(1)
        try:
            return chr(int(code, 16))
        except (ValueError, OverflowError):
            return m.group(0)
    return re.sub(pattern, repl, s)


def _char_escape_variants(s: str) -> list:
    """生成"实际特殊符号 → 字面转义序列"的组合变体。

    只对候选符号做转换（非 ASCII 且非 CJK 文字/标点的符号），
    避免把中文整体转义导致变体无法匹配（文件中的中文是原字符）。
    每个候选符号生成大小写两种十六进制形式（如 00B1 / 00b1），
    兼容文件中字面转义序列的大小写差异。候选符号数通常很小（1~3 个），
    组合数（3^k）可控。
    """
    BS = chr(92)
    idxs = []
    for i, ch in enumerate(s):
        o = ord(ch)
        if o > 127 and not (0x4E00 <= o <= 0x9FFF) and not (0x3000 <= o <= 0x303F):
            idxs.append(i)
    if not idxs:
        return []
    variants = []
    # 每个候选 2 位状态：0=原样 1=大写转义 2=小写转义（3^k 组合）
    for mask in range(1, 1 << (2 * len(idxs))):
        chars = list(s)
        changed = False
        for j, i in enumerate(idxs):
            choice = (mask >> (2 * j)) & 3
            if choice == 1:
                chars[i] = BS + "u%04X" % ord(s[i])  # 大写十六进制
                changed = True
            elif choice == 2:
                chars[i] = BS + "u%04x" % ord(s[i])  # 小写十六进制
                changed = True
        if changed:
            variants.append("".join(chars))
    return variants



def _find_unicode_escape_spans(content: str, old_str: str) -> list[tuple[int, int]]:
    """Unicode 转义宽容匹配：字面转义序列与实际字符互通。

    解决 LLM 工具调用参数 JSON 转义链路导致的 old_str 与文件不一致：
    - 文件存字面转义序列而 old_str 是实际字符
    - 文件存实际字符而 old_str 是字面转义序列

    变体优先级：原文 > 字面转实际 > 实际符号转字面（组合）。
    首次命中的变体即返回，避免不同变体匹配到不同位置造成歧义。
    """
    candidates = [old_str]
    converted = _escape_to_char(old_str)
    if converted != old_str:
        candidates.append(converted)
    for variant in _char_escape_variants(old_str):
        if variant not in candidates:
            candidates.append(variant)

    for cand in candidates:
        spans = _find_exact_spans(content, cand)
        if spans:
            return spans
    return []


def _find_best_line_repr(content: str, old_str: str, max_lines: int = 5) -> list:
    """匹配失败时，找与 old_str 最相似的若干行，返回 (行号, repr) 列表。

    repr 能显示真实转义层级（如字面转义序列 vs 实际字符），帮助 AI 定位差异。
    """
    lines = content.split("\n")
    probe = old_str[:30]
    scored = []
    for i, line in enumerate(lines, 1):
        if not line.strip():
            continue
        common = sum(1 for ch in probe if ch in line)
        score = common / max(len(probe), 1)
        scored.append((score, i, line))
    scored.sort(key=lambda x: -x[0])
    return [(i, repr(line[:80])) for score, i, line in scored[:max_lines]
            if score > 0.1]


def find_edit_matches(content: str, old_str: str) -> tuple:
    """统一查找编辑位置：精确 → 空白宽容 → Unicode 转义宽容。

    Args:
        content: 文件内容
        old_str: 要替换的文本（可能因 LLM 参数 JSON 链路导致转义层级与文件不一致）

    Returns:
        (spans, matched_text):
            spans: [(start, end), ...] 匹配位置列表（空列表表示未找到）
            matched_text: 文件中实际匹配到的文本（宽容匹配时可能与 old_str 不同，
                          用于预览显示文件真实内容）
    """
    spans = _find_exact_spans(content, old_str)
    if spans:
        return spans, old_str

    spans = _find_flex_spans(content, old_str)
    if spans:
        s, e = spans[0]
        return spans, content[s:e]

    spans = _find_unicode_escape_spans(content, old_str)
    if spans:
        s, e = spans[0]
        return spans, content[s:e]

    return [], old_str


class EditTool(BaseTool):
    """文件精确编辑工具"""

    @property
    def name(self) -> str:
        return "edit"

    @property
    def description(self) -> str:
        return (
            "精确替换文件中的文本。需要提供要替换的原始文本和新文本。"
            "old_str 必须在文件中唯一匹配；若确需替换所有匹配位置，设置 replace_all=true。"
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "要编辑的文件路径"
                },
                "old_str": {
                    "type": "string",
                    "description": "要替换的原始文本(必须唯一匹配，除非 replace_all=true)"
                },
                "new_str": {
                    "type": "string",
                    "description": "替换后的新文本"
                },
                "replace_all": {
                    "type": "boolean",
                    "description": "是否替换所有匹配位置(默认false，仅替换唯一匹配)"
                }
            },
            "required": ["file_path", "old_str", "new_str"]
        }

    def execute(self, file_path: str, old_str: str, new_str: str,
                replace_all: bool = False) -> ToolResult:
        """
        精确替换文件内容

        Args:
            file_path: 文件路径(支持 ~ 表示家目录)
            old_str: 要替换的原始文本
            new_str: 新文本
            replace_all: 是否替换所有匹配位置

        Returns:
            ToolResult: 执行结果
        """
        try:
            # 展开 ~ 为家目录
            path = Path(file_path).expanduser()

            # 检查文件是否存在
            if not path.exists():
                return ToolResult(
                    success=False,
                    output="",
                    error=f"文件不存在: {file_path}"
                )

            # 读取文件
            with open(path, 'r', encoding='utf-8') as f:
                content = f.read()

            # 1) 精确匹配
            matches = _find_exact_spans(content, old_str)
            flex_used = False
            escape_used = False

            # 2) 精确失败 → 空白字符宽容匹配（空格/非断行空格互通）
            if not matches:
                matches = _find_flex_spans(content, old_str)
                flex_used = bool(matches)

            # 3) 精确失败 → Unicode 转义宽容匹配（字面转义序列 ↔ 实际字符）
            if not matches:
                matches = _find_unicode_escape_spans(content, old_str)
                escape_used = bool(matches)

            # 检查匹配数量
            if len(matches) == 0:
                # 未找到,尝试提供可能的行号
                lines = content.split('\n')
                possible_lines = []
                for i, line in enumerate(lines, 1):
                    if old_str[:20] in line:  # 使用部分匹配
                        possible_lines.append(i)

                error_msg = f"未找到匹配的文本: {old_str[:50]}"
                if possible_lines:
                    error_msg += f"\n\n可能匹配的行号: {possible_lines[:5]}"
                # 提示文件中是否存在不可见空白字符（常见匹配失败原因）
                if chr(0xA0) in content:
                    error_msg += ("\n\n提示: 文件包含非断行空格(U+00A0)，"
                                  "可能还存在其他不可见字符差异，建议用 read 查看原文后复制粘贴")
                error_msg += "\n（已尝试空白字符/Unicode 转义宽容匹配，仍未命中）"

                # 诊断：展示 old_str 与文件最相似行的 repr（真实转义层级对比）
                best_lines = _find_best_line_repr(content, old_str)
                if best_lines:
                    error_msg += "\n\n[诊断] old_str 的 repr（可看出转义层级差异）:"
                    error_msg += f"\n  {old_str[:60]!r}"
                    error_msg += "\n[诊断] 文件中最相似的行（repr）:"
                    for line_no, line_repr in best_lines[:3]:
                        error_msg += f"\n  行 {line_no}: {line_repr}"
                    error_msg += ("\n提示: 若 old_str 含 Unicode 转义序列或特殊字符，"
                                  "请对照上面的 repr 检查转义层级是否一致")

                return ToolResult(
                    success=False,
                    output="",
                    error=error_msg
                )

            if len(matches) > 1 and not replace_all:
                # 提供每处匹配的行号和内容预览，帮助AI构造唯一匹配
                error_lines = [
                    f"找到 {len(matches)} 处匹配, old_str 必须唯一。",
                    "请提供更多上下文使其唯一，或确认需要全部替换时设置 replace_all=true。",
                    "",
                    "各匹配位置:"
                ]
                for idx, (s, e) in enumerate(matches[:8]):
                    line_no = content[:s].count('\n') + 1
                    line_start = content.rfind('\n', 0, s) + 1
                    line_end = content.find('\n', s)
                    if line_end == -1:
                        line_end = len(content)
                    preview = content[line_start:line_end].strip()
                    if len(preview) > 80:
                        preview = preview[:77] + "..."
                    error_lines.append(f"  匹配 {idx + 1} — 行 {line_no}: {preview}")
                if len(matches) > 8:
                    error_lines.append(f"  ... 还有 {len(matches) - 8} 处")

                return ToolResult(
                    success=False,
                    output="",
                    error="\n".join(error_lines)
                )

            # 执行替换（按区间替换，保留文件中的原始空白字符）
            if replace_all:
                # 从后往前替换，保持前部区间偏移有效
                new_content = content
                for (s, e) in reversed(matches):
                    new_content = new_content[:s] + new_str + new_content[e:]
                replace_count = len(matches)
            else:
                s, e = matches[0]
                new_content = content[:s] + new_str + content[e:]
                replace_count = 1

            # 写回文件
            with open(path, 'w', encoding='utf-8') as f:
                f.write(new_content)

            # 统计信息
            added_lines = new_str.count('\n')
            removed_lines = old_str.count('\n')
            line_change = added_lines - removed_lines

            # 构建简洁输出（详细预览已在确认阶段显示）
            output_lines = [
                f"✅ 已编辑文件: {file_path}",
                f"📝 替换: {len(old_str)} 字符 → {len(new_str)} 字符",
            ]
            if replace_count > 1:
                output_lines.append(f"🔁 共替换 {replace_count} 处匹配")
            if flex_used:
                output_lines.append("ℹ️  已使用空白字符宽容匹配（空格/非断行空格视为相同）")
            if escape_used:
                output_lines.append("ℹ️  已使用 Unicode 转义宽容匹配（字面转义序列与实际字符互通）")

            if line_change != 0:
                output_lines.append(f"📊 行数变化: {'+' if line_change > 0 else ''}{line_change} 行")

            return ToolResult(
                success=True,
                output="\n".join(output_lines)
            )

        except Exception as e:
            return ToolResult(
                success=False,
                output="",
                error=f"编辑文件时出错: {str(e)}"
            )
