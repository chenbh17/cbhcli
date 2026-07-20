"""文件编辑工具 - 精确字符串替换（空白字符宽容匹配兜底）"""
import re
from pathlib import Path
from cbhcli_pkg.tools.registry import BaseTool, ToolResult

# 常见空白字符：普通空格、非断行空格(U+00A0)、数字空格(U+2007)、窄非断行空格(U+202F)
_WS_CHARS = (" ", "\u00A0", "\u2007", "\u202F")
_WS_CLASS = "[ \u00A0\u2007\u202F]"  # 与 _WS_CHARS 对应的字符类（含普通空格）


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

            # 2) 精确失败 → 空白字符宽容匹配（空格/非断行空格互通）
            if not matches:
                matches = _find_flex_spans(content, old_str)
                flex_used = bool(matches)

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
                if "\u00A0" in content:
                    error_msg += ("\n\n提示: 文件包含非断行空格(U+00A0)，"
                                  "可能还存在其他不可见字符差异，建议用 read 查看原文后复制粘贴")
                error_msg += "\n（已尝试空白字符宽容匹配，仍未命中）"

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
