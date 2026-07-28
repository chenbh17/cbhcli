"""process 工具 - 查看/实时监控后台任务进度

terminal 命令超时转为后台任务后，用本工具实时监控其输出，
直到任务完成 / 用户 Ctrl+C 停止监控 / 任务运行满 1 小时自动终止。
"""
import time
from cbhcli_pkg.tools.registry import BaseTool, ToolResult
from cbhcli_pkg.core.process_manager import get_process_manager

# 内联颜色常量
_C_GREEN = "\033[32m"
_C_DIM = "\033[2m"
_C_YELLOW = "\033[33m"
_C_RED = "\033[31m"
_C_RESET = "\033[0m"

# 任务最大运行时长（秒）：超过则监控自动终止任务
MAX_RUN_SECONDS = 3600  # 1 小时
# 返回给 AI 的完整输出最大长度
_MAX_OUTPUT_CHARS = 20000


class ProcessTool(BaseTool):
    """后台任务进度查看与实时监控工具"""

    @property
    def name(self) -> str:
        return "process"

    @property
    def description(self) -> str:
        return (
            "查看和监控后台任务进度。terminal 命令超时转为后台任务后，"
            "用本工具实时监控任务输出直到完成。\n"
            "**用法**: 不传 task_id 列出所有后台任务；传入 task_id 实时监控该任务，"
            "任务完成时返回全部输出。\n"
            "**退出条件**: 任务完成 / 用户 Ctrl+C 停止监控(任务仍继续运行) / "
            "任务运行满1小时将自动终止(kill)。\n"
            "**注意**: terminal 超时返回后台任务后，请立即用本工具监控等待结果。"
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "task_id": {
                    "type": "integer",
                    "description": "后台任务ID。不传则列出所有后台任务；传入则实时监控该任务直到完成"
                }
            },
            "required": []
        }

    def execute(self, task_id: int = None, **kwargs) -> ToolResult:
        manager = get_process_manager()

        if task_id is None:
            return self._list_tasks(manager)
        return self._monitor(manager, task_id)

    # ------------------------------------------------------------------
    #  列表模式
    # ------------------------------------------------------------------

    def _list_tasks(self, manager) -> ToolResult:
        tasks = manager.list()
        if not tasks:
            return ToolResult(success=True, output="当前没有后台任务")

        lines = [f"后台任务列表（共 {len(tasks)} 个）:\n"]
        for t in tasks:
            if t.killed:
                status = f"{_C_RED}已终止{_C_RESET}"
            elif t.done:
                status = f"{_C_GREEN}已完成(退出码{t.returncode}){_C_RESET}"
            else:
                status = f"{_C_YELLOW}运行中{_C_RESET}"
            cmd = t.command if len(t.command) <= 60 else t.command[:57] + "..."
            lines.append(
                f"  [{t.task_id}] {status} 已运行 {t.elapsed_str()}  {cmd}"
            )
        lines.append(
            f"\n{_C_DIM}使用 process(task_id) 实时监控任务，"
            f"kill_process(task_id) 终止任务{_C_RESET}"
        )
        output = "\n".join(lines)
        print(output)
        # executor 只显示简短摘要（完整列表已通过 print 展示，避免重复）
        return ToolResult(
            success=True, output=output,
            display_output=f"已列出 {len(tasks)} 个后台任务",
        )

    # ------------------------------------------------------------------
    #  实时监控模式
    # ------------------------------------------------------------------

    def _monitor(self, manager, task_id: int) -> ToolResult:
        task = manager.get(task_id)
        if task is None:
            return ToolResult(
                success=False, output="",
                error=f"后台任务不存在: task_id={task_id}（可能已被清理）"
            )

        # 任务已完成：直接返回结果，不进入监控
        if task.done:
            return self._finish_result(task, prefix="任务已结束")

        print(
            f"\n{_C_GREEN}● 监控后台任务 [{task_id}]{_C_RESET} "
            f"{_C_DIM}(实时监控中){_C_RESET}"
        )
        print(f"{_C_DIM}命令: {task.command}{_C_RESET}")
        print(f"{_C_DIM}{'─' * 50}{_C_RESET}", flush=True)

        shown_chars = 0       # 已显示的字符数（增量滚动显示）
        status_shown = False  # 底部状态行是否正在显示

        def _clear_status():
            """清除底部状态行（回车 + 擦除整行）"""
            nonlocal status_shown
            if status_shown:
                print("\r\033[2K", end='', flush=True)
                status_shown = False

        def _show_status():
            """在底部打印实时状态行（不换行，下轮清除重绘）"""
            nonlocal status_shown
            print(
                f"{_C_DIM}⏱ 已运行 {task.elapsed_str()} | "
                f"Ctrl+C 停止监控 | kill_process({task_id}) 终止{_C_RESET}",
                end='', flush=True,
            )
            status_shown = True

        try:
            while True:
                # 1) 任务完成 → 打印剩余输出，返回全部内容
                if task.done:
                    _clear_status()
                    full = task.full_output()
                    if len(full) > shown_chars:
                        print(full[shown_chars:], end='', flush=True)
                    print(
                        f"\n{_C_DIM}{'─' * 50}{_C_RESET}\n"
                        f"{_C_GREEN}✅ 任务 [{task_id}] 已完成，"
                        f"退出码 {task.returncode}，"
                        f"总耗时 {task.elapsed_str()}{_C_RESET}",
                        flush=True,
                    )
                    return self._finish_result(task)

                # 2) 运行超 MAX_RUN_SECONDS(1小时) → 自动 kill
                if task.elapsed > MAX_RUN_SECONDS:
                    _clear_status()
                    self._kill_task(task)
                    limit_str = (
                        f"{MAX_RUN_SECONDS // 3600} 小时"
                        if MAX_RUN_SECONDS >= 3600
                        else f"{MAX_RUN_SECONDS // 60} 分钟"
                    )
                    print(
                        f"\n{_C_RED}⏰ 任务 [{task_id}] 运行超过 {limit_str}，"
                        f"已自动终止{_C_RESET}",
                        flush=True,
                    )
                    return ToolResult(
                        success=False,
                        output=self._trunc(task.full_output()),
                        error=(
                            f"任务 [{task_id}] 运行超过 {limit_str}已自动终止。"
                            f"命令: {task.command}"
                        ),
                    )

                # 3) 清除旧状态行 → 增量显示新输出 → 底部实时状态行
                _clear_status()
                full = task.full_output()
                if len(full) > shown_chars:
                    new_text = full[shown_chars:]
                    print(new_text, end='', flush=True)
                    shown_chars = len(full)
                    # 新输出尾部无换行时先换行，让状态行独占一行
                    if not new_text.endswith('\n'):
                        print(flush=True)
                _show_status()

                time.sleep(1)

        except KeyboardInterrupt:
            # Ctrl+C：只停止监控，任务继续后台运行
            _clear_status()
            full = task.full_output()
            if len(full) > shown_chars:
                print(full[shown_chars:], end='', flush=True)
            print(
                f"\n{_C_DIM}{'─' * 50}{_C_RESET}\n"
                f"{_C_YELLOW}⏸ 监控已停止，任务 [{task_id}] 仍在后台运行"
                f"（已运行 {task.elapsed_str()}）{_C_RESET}",
                flush=True,
            )
            return ToolResult(
                success=True,
                output=(
                    f"监控已被用户中断，任务 [{task_id}] 仍在后台继续运行"
                    f"（已运行 {task.elapsed_str()}）。\n"
                    f"可再次使用 process(task_id={task_id}) 继续监控，"
                    f"或使用 kill_process(task_id={task_id}) 终止任务。\n\n"
                    f"--- 当前输出 ---\n{self._trunc(full)}"
                ),
            )

    # ------------------------------------------------------------------
    #  辅助
    # ------------------------------------------------------------------

    def _finish_result(self, task, prefix="任务已完成") -> ToolResult:
        """任务完成时的结果（全部输出，截断保护）"""
        full = self._trunc(task.full_output())
        ok = (task.returncode == 0)
        summary = (
            f"{prefix}: [{task.task_id}] 退出码 {task.returncode}，"
            f"总耗时 {task.elapsed_str()}\n命令: {task.command}\n\n"
            f"--- 全部输出 ---\n{full if full.strip() else '(无输出)'}"
        )
        if ok:
            return ToolResult(success=True, output=summary)
        return ToolResult(
            success=False, output=summary,
            error=f"任务 [{task.task_id}] 退出码 {task.returncode}",
        )

    def _kill_task(self, task):
        """终止任务进程组"""
        import os
        try:
            os.killpg(task.process.pid, 9)
        except (ProcessLookupError, PermissionError):
            try:
                task.process.kill()
            except Exception:
                pass
        task.killed = True
        try:
            task.process.wait(timeout=3)
        except Exception:
            pass

    def _trunc(self, text: str) -> str:
        if len(text) > _MAX_OUTPUT_CHARS:
            return (
                f"(输出过长，已截断前部，共 {len(text)} 字符)\n...\n"
                + text[-_MAX_OUTPUT_CHARS:]
            )
        return text
