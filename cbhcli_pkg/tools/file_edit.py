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
        return "精确替换文件中的文本。需要提供要替换的原始文本和新文本。"
    
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
                    "description": "要替换的原始文本(必须唯一匹配)"
                },
                "new_str": {
                    "type": "string",
                    "description": "替换后的新文本"
                }
            },
            "required": ["file_path", "old_str", "new_str"]
        }
    
    def execute(self, file_path: str, old_str: str, new_str: str) -> ToolResult:
        """
        精确替换文件内容
        
        Args:
            file_path: 文件路径(支持 ~ 表示家目录)
            old_str: 要替换的原始文本
            new_str: 新文本
            
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
            
            if len(matches) > 1:
                return ToolResult(
                    success=False,
                    output="",
                    error=f"找到 {len(matches)} 处匹配,old_str必须唯一。请提供更多上下文使其唯一。"
                )
            
            # 执行替换
            new_content = content.replace(old_str, new_str, 1)
            
            # 写回文件
            with open(path, 'w', encoding='utf-8') as f:
                f.write(new_content)
            
            # 统计信息
            added_lines = new_str.count('\n')
            removed_lines = old_str.count('\n')
            line_change = added_lines - removed_lines
            
            # 构建输出
            output_lines = [
                f"✅ 已编辑文件: {file_path}",
                f"📝 替换: {len(old_str)} 字符 → {len(new_str)} 字符",
            ]
            
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
