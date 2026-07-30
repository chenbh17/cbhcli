"""MCP 工具适配器 - 将 MCP 工具转换为 CBHCLI BaseTool"""
import json
from cbhcli_pkg.tools.registry import BaseTool, ToolResult
from cbhcli_pkg.core.mcp_client import MCPClient


class MCPToolAdapter(BaseTool):
    """MCP 工具适配器
    
    将 MCP 服务器上的工具包装为 CBHCLI 的 BaseTool。
    """
    
    def __init__(self, mcp_client: MCPClient, tool_info: dict, server_name: str):
        """
        Args:
            mcp_client: MCP 客户端
            tool_info: MCP 工具信息 {name, description, inputSchema}
            server_name: MCP 服务器名称（用于标识来源）
        """
        self._mcp_client = mcp_client
        self._tool_info = tool_info
        self._server_name = server_name
    
    @property
    def name(self) -> str:
        """工具名称 - 使用 mcp_前缀避免冲突"""
        return f"mcp_{self._tool_info['name']}"
    
    @property
    def description(self) -> str:
        """工具描述"""
        desc = self._tool_info.get("description", "")
        return f"[MCP: {self._server_name}] {desc}"
    
    @property
    def parameters(self) -> dict:
        """JSON Schema 参数定义"""
        return self._tool_info.get("inputSchema", {
            "type": "object",
            "properties": {},
            "required": []
        })
    
    def execute(self, **kwargs) -> ToolResult:
        """执行 MCP 工具
        
        Args:
            **kwargs: 工具参数
            
        Returns:
            ToolResult: 执行结果
        """
        try:
            # 调用 MCP 工具
            results = self._mcp_client.call_tool(self._tool_info["name"], kwargs)
            
            # 确保 results 是列表
            if not isinstance(results, list):
                results = [results] if results else []
            
            # 解析 MCP Content 数组
            output_parts = []
            for content in results:
                if isinstance(content, str):
                    # 直接是字符串，确保正确编码
                    output_parts.append(content)
                elif isinstance(content, dict):
                    content_type = content.get("type", "")
                    if content_type == "text":
                        text = content.get("text", "")
                        # 确保文本使用 UTF-8 编码
                        if isinstance(text, bytes):
                            text = text.decode('utf-8')
                        output_parts.append(text)
                    elif content_type == "image":
                        output_parts.append(f"[图片: {content.get('mimeType', 'unknown')}]")
                    elif content_type == "resource":
                        resource = content.get("resource", {})
                        output_parts.append(f"[资源: {resource.get('uri', 'unknown')}]")
                    else:
                        # 使用 ensure_ascii=False 确保中文正常显示
                        output_parts.append(json.dumps(content, ensure_ascii=False))
                else:
                    output_parts.append(str(content))
            
            output = "\n".join(output_parts)
            
            # 如果输出过长，提供摘要
            if len(output) > 10000:
                # 尝试解析为 JSON 数组并提供摘要
                try:
                    data = json.loads(output)
                    if isinstance(data, list):
                        summary = f"共 {len(data)} 条记录\n\n"
                        # 显示前 5 条
                        for i, item in enumerate(data[:5]):
                            if isinstance(item, dict):
                                summary += f"--- 记录 {i+1} ---\n"
                                for k, v in item.items():
                                    val_str = str(v)[:200]
                                    summary += f"{k}: {val_str}\n"
                                summary += "\n"
                        if len(data) > 5:
                            summary += f"... 还有 {len(data) - 5} 条记录（完整数据已保留，但此处省略显示）"
                        output = summary
                except json.JSONDecodeError:
                    # 不是 JSON，直接截断
                    output = output[:10000] + f"\n\n...（输出过长，已截断。总长度: {len(output)} 字符）"
            
            return ToolResult(
                success=True,
                output=output if output else "工具执行成功，无输出"
            )
            
        except Exception as e:
            return ToolResult(
                success=False,
                output="",
                error=f"MCP 工具执行失败: {str(e)}"
            )
