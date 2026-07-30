"""kill_process 工具 - 终止后台任务

用于终止运行时间过长或不再需要后台任务。
触发场景：任务运行超 1 小时（process 监控自动触发）、
用户明确要求终止、或 AI 判断任务失控需要终止。
"""
import os
from cbhcli_pkg.tools.registry import BaseTool, ToolResult
from cbhcli_pkg.core.process_manager import get_process_manager


class KillProcessTool(BaseTool):
    """后台任务终止工具"""

    @property
    def name(self) -> str:
        return "kill_process"

    @property
    def description(self) -> str:
        return (
            "终止指定的后台任务（kill 整个进程组）。\n"
            "**触发场景**: 任务运行时间过长(如超过1小时)、用户明确要求终止、"
            "或任务失控/不再需要时。\n"
            "**注意**: 终止是不可逆的，终止前可用 process(task_id) 查看任务当前输出。"
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "task_id": {
                    "type": "integer",
                    "description": "要终止的后台任务ID"
                }
            },
            "required": ["task_id"]
        }

    def execute(self, task_id: int = None, **kwargs) -> ToolResult:
        if task_id is None:
            return ToolResult(
                success=False, output="",
                error="缺少必需参数: task_id"
            )

        manager = get_process_manager()
        task = manager.get(task_id)

        if task is None:
            return ToolResult(
                success=False, output="",
                error=f"后台任务不存在: task_id={task_id}（可能已被清理）"
            )

        if task.killed:
            return ToolResult(
                success=True,
                output=f"任务 [{task_id}] 此前已被终止，无需重复操作"
            )

        if task.done:
            return ToolResult(
                success=True,
                output=(
                    f"任务 [{task_id}] 已自行结束（退出码 {task.returncode}，"
                    f"耗时 {task.elapsed_str()}），无需终止"
                )
            )

        # kill 整个进程组（shell + 所有子进程）
        try:
            os.killpg(task.process.pid, 9)  # SIGKILL
        except (ProcessLookupError, PermissionError):
            try:
                task.process.kill()
            except Exception as e:
                return ToolResult(
                    success=False, output="",
                    error=f"终止任务 [{task_id}] 失败: {e}"
                )

        task.killed = True
        try:
            task.process.wait(timeout=3)
        except Exception:
            pass

        elapsed = task.elapsed_str()
        output_preview = task.full_output()
        if len(output_preview) > 3000:
            output_preview = "...\n" + output_preview[-3000:]

        msg = (
            f"🛑 已终止后台任务 [{task_id}]（运行了 {elapsed}）\n"
            f"命令: {task.command}"
        )
        if output_preview.strip():
            msg += f"\n\n--- 终止前输出 ---\n{output_preview.rstrip()}"
        return ToolResult(success=True, output=msg)
