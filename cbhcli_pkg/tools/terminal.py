"""终端工具 - 执行shell命令"""
import os
import subprocess
import threading
from cbhcli_pkg.tools.registry import BaseTool, ToolResult


def _kill_process_group(process):
    """杀掉进程及其整个进程组

    start_new_session=True 使 shell 独立成进程组（子进程都在组内），
    killpg 可一网打尽；失败时回退只杀 shell 本身。
    """
    try:
        os.killpg(process.pid, 9)  # SIGKILL
    except (ProcessLookupError, PermissionError):
        try:
            process.kill()
        except Exception:
            pass


def _pipe_reader(fd, chunks: list):
    """持续读取管道数据到 chunks（后台线程）

    逐块读取（每块立即保存），而不是 read() 一次性读到 EOF：
    孙进程持有管道写端不关闭时，read() 会永远阻塞且已读数据不返回，
    逐块读可保证 join 超时放弃时已读数据不丢。

    Args:
        fd: 管道文件描述符
        chunks: 数据块累积列表（bytes）
    """
    try:
        while True:
            data = os.read(fd, 4096)
            if not data:
                break  # EOF：所有写端都已关闭
            chunks.append(data)
    except (OSError, ValueError):
        pass


class TerminalTool(BaseTool):
    """终端命令执行工具"""

    def __init__(self):
        # v5.2.9：支持外部中断（Web 端用户点击中断按钮时杀掉正在运行的子进程）
        self._current_process = None        # 当前正在运行的子进程
        self._interrupt_requested = False   # 中断标记（区分"被中断"与"正常失败"）

    def interrupt(self):
        """外部中断：杀掉当前正在运行的子进程（v5.2.9）。

        Web 端会话 abort 时由服务端在工具执行期间调用。杀掉后
        execute 中的 process.wait() 立即返回，工具以"被用户中断"结果结束。
        """
        self._interrupt_requested = True
        proc = self._current_process
        if proc is not None:
            _kill_process_group(proc)

    @property
    def name(self) -> str:
        return "terminal"

    @property
    def description(self) -> str:
        return (
            "执行终端命令。可以执行任何shell命令,包括文件操作、程序运行等。\n"
            "**超时机制**: 主进程退出后立即返回结果（nohup/& 后台进程不空等）。"
            "超过 timeout(默认30秒) 未完成时进程【不会被终止】，而是转为后台任务继续运行，"
            "此时请立即使用 process 工具实时监控进度并等待完成。\n"
            "**注意**: 需要持续交互的命令（如 cbhcli、vim、nano、top、htop、less、"
            "ssh、以及无参数的 python/bash 进入REPL）请勿执行。\n"
            "sudo 密码等通过 /dev/tty 的交互是支持的（用户可直接在终端输入密码后继续）。"
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "要执行的shell命令"
                },
                "timeout": {
                    "type": "integer",
                    "description": "超时时间(秒)，默认30。编译/下载等耗时命令可适当调大"
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
            # stdin 默认继承父进程：sudo 等需要密码的命令可通过 /dev/tty
            # 与用户直接交互（提示和密码不经过 stdout/stderr 管道）
            # start_new_session：让 shell 独立成进程组，超时时可 killpg
            # 杀掉整组（否则只杀 shell，子进程变孤儿继续持有管道）
            self._interrupt_requested = False
            process = subprocess.Popen(
                command,
                shell=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=True,
            )
            self._current_process = process  # 供 interrupt() 外部中断

            # 后台读线程持续 drain 两个管道：
            # 1) 防止输出超过64KB管道缓冲导致主进程阻塞在write（死锁）
            # 2) 主进程退出后能快速收取残余输出
            out_chunks, err_chunks = [], []
            t_out = threading.Thread(
                target=_pipe_reader,
                args=(process.stdout.fileno(), out_chunks),
                daemon=True,
            )
            t_err = threading.Thread(
                target=_pipe_reader,
                args=(process.stderr.fileno(), err_chunks),
                daemon=True,
            )
            t_out.start()
            t_err.start()

            try:
                # 只等主进程退出，【不等管道 EOF】
                # （旧实现用 communicate：nohup/& 派生的孙进程持有管道写端时，
                #   主进程已退出仍空等 EOF 直到超时，表现为"已完成却无限等待"）
                process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                # 超时不 kill！注册为后台任务继续运行（读线程仍在 drain 输出），
                # 提示 AI 使用 process 工具实时监控、kill_process 工具手动终止
                from cbhcli_pkg.core.process_manager import get_process_manager
                task = get_process_manager().register(
                    command, process, out_chunks, err_chunks
                )
                # 收一下当前已有输出作为预览（不阻塞，读线程继续运行）
                t_out.join(0.5)
                t_err.join(0.5)
                preview = task.full_output()
                if len(preview) > 1500:
                    preview = "...\n" + preview[-1500:]
                msg = (
                    f"⏳ 命令执行超过 {timeout} 秒，已转为后台任务继续运行"
                    f"（task_id={task.task_id}），进程未被终止。\n"
                    f"命令: {command}\n"
                )
                if preview.strip():
                    msg += f"\n--- 当前已有输出 ---\n{preview.rstrip()}\n---\n"
                msg += (
                    f"\n请立即使用 process 工具（task_id={task.task_id}）实时监控任务进度，"
                    f"等待任务完成并获取全部输出；"
                    f"如需终止可使用 kill_process 工具（task_id={task.task_id}）。"
                )
                return ToolResult(success=True, output=msg)
            except KeyboardInterrupt:
                # Ctrl+C：SIGINT 只发给 cbhcli 前台进程组，start_new_session
                # 使子进程收不到（不变孤儿需手动杀）；杀掉后 re-raise 让
                # app 主循环捕获（打印"操作被中断"回到输入框）
                self._current_process = None
                _kill_process_group(process)
                process.wait()
                # 管道已 EOF，join 快速返回，残余输出随 raise 丢弃
                t_out.join(1.0)
                t_err.join(1.0)
                raise

            # 主进程结束后收取残余输出；孙进程仍持有写端时
            # join 超时直接放弃（daemon 线程不阻塞退出，已读数据不丢）
            self._current_process = None
            t_out.join(1.0)
            t_err.join(1.0)

            stdout = b''.join(out_chunks).decode('utf-8', errors='replace')
            stderr = b''.join(err_chunks).decode('utf-8', errors='replace')

            # 构建输出
            output = ""
            if stdout:
                output += stdout
            if stderr:
                output += stderr

            # v5.2.9：被用户中断（Web 端 abort）——进程已被 interrupt() 杀掉，
            # 明确返回"被中断"结果（区别于正常失败，避免模型反思重试同一命令）
            if self._interrupt_requested:
                self._interrupt_requested = False
                partial = output.strip()
                return ToolResult(
                    success=False,
                    output=partial or "（无输出）",
                    error="命令已被用户中断（进程已终止）"
                            + (f"\n\n中断前的输出:\n{partial}" if partial else ""))

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
