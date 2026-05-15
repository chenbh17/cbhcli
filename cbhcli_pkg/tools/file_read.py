"""文件读取工具"""
from pathlib import Path
from cbhcli_pkg.tools.registry import BaseTool, ToolResult


class ReadTool(BaseTool):
    """文件内容读取工具"""
    
    @property
    def name(self) -> str:
        return "read"
    
    @property
    def description(self) -> str:
        return "读取文件内容。可以读取任何文本文件,支持指定行范围。"
    
    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "要读取的文件路径"
                },
                "start_line": {
                    "type": "integer",
                    "description": "起始行号(从1开始,可选)"
                },
                "end_line": {
                    "type": "integer",
                    "description": "结束行号(可选)"
                }
            },
            "required": ["file_path"]
        }
    
    def execute(self, file_path: str, start_line: int = None, 
                end_line: int = None) -> ToolResult:
        """
        读取文件内容
        
        Args:
            file_path: 文件路径(支持 ~ 表示家目录)
            start_line: 起始行号(可选)
            end_line: 结束行号(可选)
            
        Returns:
            ToolResult: 文件内容
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
            
            # 检查是否为文件
            if not path.is_file():
                return ToolResult(
                    success=False,
                    output="",
                    error=f"不是文件: {file_path}"
                )
            
            # 读取文件
            with open(path, 'r', encoding='utf-8') as f:
                lines = f.readlines()
            
            total_lines = len(lines)
            
            # 处理行范围
            if start_line is not None:
                start_idx = max(0, start_line - 1)
            else:
                start_idx = 0
            
            if end_line is not None:
                end_idx = min(len(lines), end_line)
            else:
                end_idx = len(lines)
            
            selected_lines = lines[start_idx:end_idx]
            content = ''.join(selected_lines)
            
            # 构建输出
            output_lines = []
            output_lines.append(f"📄 文件: {file_path}")
            output_lines.append(f"📊 总行数: {total_lines}")
            
            if start_line or end_line:
                output_lines.append(f"📍 显示范围: 第{start_idx+1}-{end_idx}行")
            
            output_lines.append("")
            output_lines.append("--- 文件内容 ---")
            
            # 添加行号
            for i, line in enumerate(selected_lines, start=start_idx + 1):
                # 保留尾部空格，只去除换行符（因为行号格式化会自动换行）
                output_lines.append(f"{i:4d} | {line.rstrip(chr(10)).rstrip(chr(13))}")
            
            output_lines.append("--- 结束 ---")
            
            return ToolResult(
                success=True,
                output="\n".join(output_lines)
            )
            
        except UnicodeDecodeError:
            return ToolResult(
                success=False,
                output="",
                error=f"无法读取文件: 不是UTF-8编码的文本文件"
            )
        except Exception as e:
            return ToolResult(
                success=False,
                output="",
                error=f"读取文件时出错: {str(e)}"
            )
