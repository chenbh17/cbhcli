"""斜杠命令解析器"""
from dataclasses import dataclass
from typing import Callable, Optional


@dataclass
class SlashCommand:
    """斜杠命令定义"""
    name: str
    description: str
    usage: str
    handler: Callable  # 处理函数
    requires_agent: bool = False  # 是否需要当前Agent


class SlashCommandParser:
    """斜杠命令解析器"""
    
    def __init__(self):
        self._commands: dict[str, SlashCommand] = {}
    
    def register(self, command: SlashCommand) -> None:
        """注册命令"""
        self._commands[command.name] = command
    
    def parse(self, input_text: str) -> Optional[tuple]:
        """
        解析输入文本
        
        Args:
            input_text: 用户输入
            
        Returns:
            (command_name, args) 或 None
        """
        input_text = input_text.strip()
        
        if not input_text.startswith('/'):
            return None
        
        # 移除开头的 '/'
        input_text = input_text[1:]
        
        # 分割命令和参数
        parts = input_text.split(None, 1)
        command_name = parts[0].lower()
        args = parts[1] if len(parts) > 1 else ""
        
        return (command_name, args)
    
    def execute(self, input_text: str) -> tuple[bool, str]:
        """
        执行命令
        
        Args:
            input_text: 用户输入
            
        Returns:
            (success: bool, output: str)
        """
        result = self.parse(input_text)
        
        if result is None:
            return (False, "")
        
        command_name, args = result
        
        if command_name not in self._commands:
            return (False, f"❌ 未知命令: /{command_name}\n输入 /help 查看可用命令")
        
        command = self._commands[command_name]
        
        try:
            output = command.handler(args)
            return (True, output)
        except Exception as e:
            return (False, f"❌ 命令执行失败: {str(e)}")
    
    def get_help_text(self) -> str:
        """获取帮助文本"""
        lines = ["📖 可用命令:\n"]
        
        for cmd in sorted(self._commands.values(), key=lambda x: x.name):
            lines.append(f"  /{cmd.name.ljust(15)} {cmd.description}")
            if cmd.usage:
                lines.append(f"  {''.ljust(18)}用法: {cmd.usage}")
            lines.append("")
        
        return "\n".join(lines)
    
    def get_command(self, name: str) -> Optional[SlashCommand]:
        """获取命令定义"""
        return self._commands.get(name)
    
    def get_all_commands(self) -> dict[str, SlashCommand]:
        """获取所有已注册命令"""
        return self._commands
