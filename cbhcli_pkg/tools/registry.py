"""工具注册中心 - 统一工具管理"""
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional, Any


@dataclass
class ToolResult:
    """工具执行结果"""
    success: bool
    output: str
    error: Optional[str] = None
    metadata: Optional[dict] = None


class BaseTool(ABC):
    """工具抽象基类"""
    
    @property
    @abstractmethod
    def name(self) -> str:
        """工具名称"""
        pass
    
    @property
    @abstractmethod
    def description(self) -> str:
        """工具描述,用于系统提示"""
        pass
    
    @property
    @abstractmethod
    def parameters(self) -> dict:
        """JSON Schema格式的参数定义"""
        pass
    
    @abstractmethod
    def execute(self, **kwargs) -> ToolResult:
        """
        执行工具
        
        Args:
            **kwargs: 工具参数
            
        Returns:
            ToolResult: 执行结果
        """
        pass


class ToolRegistry:
    """工具注册中心"""
    
    def __init__(self):
        self._tools: dict[str, BaseTool] = {}
    
    def register(self, tool: BaseTool) -> None:
        """注册工具"""
        self._tools[tool.name] = tool
    
    def unregister(self, name: str) -> None:
        """注销工具"""
        if name in self._tools:
            del self._tools[name]
    
    def get(self, name: str) -> Optional[BaseTool]:
        """获取工具"""
        return self._tools.get(name)
    
    def execute(self, name: str, **kwargs) -> ToolResult:
        """
        执行工具
        
        Args:
            name: 工具名称
            **kwargs: 工具参数
            
        Returns:
            ToolResult: 执行结果
        """
        tool = self.get(name)
        if not tool:
            return ToolResult(
                success=False,
                output="",
                error=f"未知工具: {name}"
            )
        
        try:
            return tool.execute(**kwargs)
        except Exception as e:
            return ToolResult(
                success=False,
                output="",
                error=f"工具执行失败: {str(e)}"
            )
    
    def get_tool_descriptions(self) -> str:
        """获取所有工具的描述(用于系统提示)"""
        if not self._tools:
            return ""
        
        descriptions = []
        for tool in self._tools.values():
            descriptions.append(
                f"- {tool.name}: {tool.description}\n"
                f"  参数: {tool.parameters}"
            )
        
        return "\n".join(descriptions)
    
    def get_available_tools(self) -> list[str]:
        """获取可用工具名称列表"""
        return list(self._tools.keys())
    
    def get_openai_tools(self) -> list[dict]:
        """获取 OpenAI function calling 格式的工具定义列表
        
        Returns:
            工具定义列表，格式为 OpenAI tools 参数格式
        """
        tools = []
        for tool in self._tools.values():
            tools.append({
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.parameters
                }
            })
        return tools
    
    def fuzzy_get(self, name: str) -> Optional[BaseTool]:
        """模糊匹配获取工具（当精确匹配失败时的回退）
        
        尝试策略：
        1. 精确匹配
        2. 添加 mcp_ 前缀
        3. 去掉 mcp_ 前缀后重新匹配
        4. 忽略大小写匹配
        
        Returns:
            匹配到的工具或 None
        """
        # 精确匹配
        tool = self._tools.get(name)
        if tool:
            return tool
        
        # 添加 mcp_ 前缀
        if not name.startswith("mcp_"):
            tool = self._tools.get(f"mcp_{name}")
            if tool:
                return tool
        
        # 忽略大小写匹配
        name_lower = name.lower()
        for tool_name, tool in self._tools.items():
            if tool_name.lower() == name_lower:
                return tool
        
        return None
