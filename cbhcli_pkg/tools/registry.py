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
    display_output: Optional[str] = None  # 终端显示内容（None则显示output）
    images: Optional[list] = None  # 图片列表（base64），由 ai_handler/web 追加为带图用户消息直发主模型
    duration_ms: int = 0  # 执行耗时（毫秒），由 tool_executor 填充
    display_files: Optional[list] = None  # AI 向用户展示的文件列表 [{path, filename, is_image, url}]，仅 Web 端使用
    harness_findings: Optional[list] = None  # 领域 Harness 检查发现 [{level,code,message,fix}]，
    # 由 cbhpacks 工具产出；CLI 醒目横幅 / Web 徽标+警告块 / PostToolUse hooks payload 三处消费


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
        self._disabled_tools: set[str] = set()  # 被禁用的工具名称集合

    def set_disabled_tools(self, disabled: list[str]) -> None:
        """设置被禁用的工具列表

        Args:
            disabled: 被禁用的工具名称列表
        """
        self._disabled_tools = set(disabled)

    def is_disabled(self, name: str) -> bool:
        """检查工具是否被禁用"""
        return name in self._disabled_tools

    def _get_active_tools(self) -> dict[str, BaseTool]:
        """获取当前活跃（未被禁用）的工具"""
        return {name: tool for name, tool in self._tools.items()
                if name not in self._disabled_tools}
    
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

        if name in self._disabled_tools:
            return ToolResult(
                success=False,
                output="",
                error=f"工具 '{name}' 已被禁用，请联系用户使用 /tools on 开启"
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
        """获取所有活跃工具的描述(用于系统提示)"""
        active = self._get_active_tools()
        if not active:
            return ""

        descriptions = []
        for tool in active.values():
            descriptions.append(
                f"- {tool.name}: {tool.description}\n"
                f"  参数: {tool.parameters}"
            )

        return "\n".join(descriptions)
    
    def get_available_tools(self) -> list[str]:
        """获取活跃（未被禁用）的工具名称列表"""
        return list(self._get_active_tools().keys())
    
    def get_openai_tools(self) -> list[dict]:
        """获取 OpenAI function calling 格式的工具定义列表
        
        Returns:
            工具定义列表，格式为 OpenAI tools 参数格式
        """
        tools = []
        for tool in self._get_active_tools().values():
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
