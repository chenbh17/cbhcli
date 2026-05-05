"""工具执行器"""
import json
import sys
from typing import Optional, Callable

from cbhcli_pkg.tools.registry import ToolRegistry, ToolResult
from cbhcli_pkg.core.constants import (
    MAX_TOOL_OUTPUT_LENGTH, TOOL_PREVIEW_LENGTH,
    C_TOOL_DOT, C_TOOL_GREEN, C_TOOL_CMD, C_TOOL_RESULT,
    C_DIM, C_SEP, C_AI_HINT, C_ERROR, C_RESET
)
from cbhcli_pkg.core.errors import ToolExecutionError


class ToolExecutor:
    """处理工具调用执行
    
    负责：
    - 工具执行前的确认
    - 工具执行
    - 结果格式化和输出
    """
    
    def __init__(self, tool_registry: ToolRegistry):
        """
        Args:
            tool_registry: 工具注册中心
        """
        self.tool_registry = tool_registry
        self.no_more_confirmations = False
        self.verbose = False
        self._on_tool_execute: Optional[Callable] = None
    
    def set_verbose(self, verbose: bool):
        """设置详细输出模式"""
        self.verbose = verbose
    
    def set_confirmation_mode(self, no_more_confirmations: bool):
        """设置是否跳过确认"""
        self.no_more_confirmations = no_more_confirmations
    
    def execute(self, tool_name: str, arguments: dict) -> ToolResult:
        """执行工具
        
        Args:
            tool_name: 工具名称
            arguments: 工具参数
            
        Returns:
            ToolResult: 执行结果
        """
        return self.tool_registry.execute(tool_name, **arguments)
    
    # 自带显示的工具（跳过 executor 的头部和结果显示）
    _SELF_DISPLAY_TOOLS = {"Todo"}

    def execute_with_display(
        self, 
        tool_name: str, 
        arguments: dict,
        tool_call_id: Optional[str] = None
    ) -> ToolResult:
        """执行工具并显示结果
        
        Args:
            tool_name: 工具名称
            arguments: 工具参数
            tool_call_id: 工具调用ID（用于OpenAI格式）
            
        Returns:
            ToolResult: 执行结果
        """
        self_display = tool_name in self._SELF_DISPLAY_TOOLS
        
        # 显示工具调用（自带显示的工具跳过）
        if not self_display:
            self._display_tool_call(tool_name, arguments)
        
        # 执行前确认
        if not self._confirm_execution(tool_name):
            result = ToolResult(
                success=False, 
                output="", 
                error="用户取消了执行"
            )
        else:
            # 执行工具
            result = self.execute(tool_name, arguments)
        
        # 显示结果（自带显示的工具跳过）
        if not self_display:
            self._display_result(result)
        
        # 回调
        if self._on_tool_execute:
            self._on_tool_execute(tool_name, arguments, result, tool_call_id)
        
        return result
    
    def _display_tool_call(self, tool_name: str, arguments: dict):
        """显示工具调用信息"""
        cmd_preview = self._get_tool_preview(tool_name, arguments)
        
        print(f"\n{C_SEP}{'─' * 60}")
        if cmd_preview:
            print(f"{C_TOOL_DOT}● {C_TOOL_GREEN}{tool_name}{C_RESET}  {C_TOOL_CMD}{cmd_preview}{C_RESET}")
        else:
            print(f"{C_TOOL_DOT}● {C_TOOL_GREEN}{tool_name}{C_RESET}")
        
        if self.verbose:
            print(f"{C_SEP}   完整参数: {json.dumps(arguments, ensure_ascii=False)}{C_RESET}")
    
    def _get_tool_preview(self, tool_name: str, arguments: dict) -> str:
        """获取工具调用的预览字符串"""
        if tool_name == "terminal":
            cmd = arguments.get("command", "")
            if not cmd:
                cmd = arguments.get("cmd", "") or arguments.get("shell", "")
            if len(cmd) > 80 and not self.verbose:
                return cmd[:80] + "..."
            return cmd
        elif tool_name in ("read", "write", "edit"):
            path = arguments.get("path", arguments.get("file_path", ""))
            return path
        elif tool_name == "grep":
            pattern = arguments.get("pattern", "")
            path = arguments.get("path", ".")
            include = arguments.get("include", "")
            preview = f"/{pattern}/ in {path}"
            if include:
                preview += f" ({include})"
            return preview
        elif tool_name == "glob":
            return arguments.get("pattern", "")
        elif tool_name == "ask_user":
            return arguments.get("question", "")[:60]
        return ""
    
    def _confirm_execution(self, tool_name: str) -> bool:
        """确认是否执行工具"""
        if self.no_more_confirmations:
            return True
        
        # 只读/交互工具跳过确认
        if tool_name in ("grep", "glob", "ask_user", "read", "Todo",
                         "memory_search", "knowledge_base"):
            return True
        
        try:
            confirm = input(f"\n{C_AI_HINT}确认执行 {tool_name}? [Y/n/all]: {C_RESET}")
        except (EOFError, KeyboardInterrupt):
            return False
        
        confirm = confirm.strip().lower()
        
        if confirm == "all":
            self.no_more_confirmations = True
            return True
        elif confirm in ("n", "no"):
            return False
        
        return True
    
    def _display_result(self, result: ToolResult):
        """显示执行结果"""
        if result.success:
            output = result.output[:MAX_TOOL_OUTPUT_LENGTH] if result.output else ""
            
            if self.verbose:
                output_preview = output
            else:
                output_preview = output[:TOOL_PREVIEW_LENGTH]
                if len(output) > TOOL_PREVIEW_LENGTH:
                    output_preview += "..."
            
            print(f"{C_TOOL_RESULT}   → {output_preview}{C_RESET}")
        else:
            error_msg = result.error or "未知错误"
            print(f"{C_ERROR}   → 失败: {error_msg}{C_RESET}")
        
        print(f"{C_SEP}{'─' * 60}{C_RESET}")
    
    def on_tool_execute(self, callback: Callable):
        """设置工具执行回调
        
        Args:
            callback: 回调函数 (tool_name, arguments, result, tool_call_id)
        """
        self._on_tool_execute = callback
