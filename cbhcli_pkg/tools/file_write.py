"""文件写入工具"""
from pathlib import Path
from cbhcli_pkg.tools.registry import BaseTool, ToolResult


class WriteTool(BaseTool):
    """文件创建/覆盖工具"""

    @property
    def name(self) -> str:
        return "write"

    @property
    def description(self) -> str:
        return "创建新文件或覆盖现有文件的内容。⚠️ 警告: 会完全覆盖现有文件!"

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "要创建或覆盖的文件路径"
                },
                "content": {
                    "type": "string",
                    "description": "要写入文件的内容"
                }
            },
            "required": ["file_path", "content"]
        }

    def execute(self, file_path: str, content: str) -> ToolResult:
        """
        写入文件内容

        Args:
            file_path: 文件路径(支持 ~ 表示家目录)
            content: 文件内容

        Returns:
            ToolResult: 执行结果
        """
        try:
            # 展开 ~ 为家目录
            path = Path(file_path).expanduser()

            # 检查文件是否已存在
            existed = path.exists()

            # 确保父目录存在
            path.parent.mkdir(parents=True, exist_ok=True)

            # 写入文件
            with open(path, 'w', encoding='utf-8') as f:
                f.write(content)

            # 统计信息
            lines = content.count('\n') + 1
            chars = len(content)

            # 构建简洁输出（详细预览已在确认阶段显示）
            output_lines = []
            if existed:
                output_lines.append(f"⚠️  已覆盖文件: {file_path}")
            else:
                output_lines.append(f"✅ 已创建文件: {file_path}")

            output_lines.append(f"📊 文件大小: {chars} 字符, {lines} 行")

            return ToolResult(
                success=True,
                output="\n".join(output_lines)
            )

        except Exception as e:
            return ToolResult(
                success=False,
                output="",
                error=f"写入文件时出错: {str(e)}"
            )
