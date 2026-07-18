"""文件编辑工具 - 精确字符串替换"""
from pathlib import Path
from cbhcli_pkg.tools.registry import BaseTool, ToolResult


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

            # 查找匹配
            matches = []
            start = 0
            while True:
                pos = content.find(old_str, start)
                if pos == -1:
                    break
                matches.append(pos)
                start = pos + 1

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
                for idx, pos in enumerate(matches[:8]):
                    line_no = content[:pos].count('\n') + 1
                    line_start = content.rfind('\n', 0, pos) + 1
                    line_end = content.find('\n', pos)
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

            # 执行替换
            if replace_all:
                new_content = content.replace(old_str, new_str)
                replace_count = len(matches)
            else:
                new_content = content.replace(old_str, new_str, 1)
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
