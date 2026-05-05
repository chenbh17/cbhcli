"""终端工具 - 执行shell命令"""
import subprocess
import signal
from cbhcli_pkg.tools.registry import BaseTool, ToolResult


class TerminalTool(BaseTool):
    """终端命令执行工具"""
    
    @property
    def name(self) -> str:
        return "terminal"
    
    @property
    def description(self) -> str:
        return "执行终端命令。可以执行任何shell命令,包括文件操作、程序运行等。"
    
    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "要执行的shell命令"
                }
            },
            "required": ["command"]
        }
    
    def execute(self, command: str, timeout: int = 30) -> ToolResult:
        """
        执行终端命令
        
        Args:
            command: 要执行的命令
            timeout: 超时时间(秒)
            
        Returns:
            ToolResult: 执行结果
        """
        try:
            # 显示执行的命令
            commands = command.split(' && ')
            cmd_display = "\n".join([f"[运行] {cmd.strip()}" for cmd in commands if cmd.strip()])
            
            # 执行命令
            process = subprocess.Popen(
                command,
                shell=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True
            )
            
            # 设置超时处理
            try:
                stdout, stderr = process.communicate(timeout=timeout)
            except subprocess.TimeoutExpired:
                process.kill()
                stdout, stderr = process.communicate()
                return ToolResult(
                    success=False,
                    output=stdout,
                    error=f"命令执行超时({timeout}秒)"
                )
            
            # 构建输出
            output = ""
            if stdout:
                output += stdout
            if stderr:
                output += stderr
            
            success = process.returncode == 0
            
            if success:
                return ToolResult(
                    success=True,
                    output=output if output else "命令执行成功,无输出"
                )
            else:
                # 失败时，error 包含退出码和详细输出
                error_detail = f"命令执行失败,退出码 {process.returncode}"
                if output:
                    error_detail += f"\n\n详细输出:\n{output}"
                return ToolResult(
                    success=False,
                    output=output if output else "命令执行失败,无输出",
                    error=error_detail
                )
            
        except Exception as e:
            return ToolResult(
                success=False,
                output="",
                error=f"执行命令时出错: {str(e)}"
            )
