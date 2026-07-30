"""后台任务进程管理器

terminal 工具执行命令超时后，进程不再 kill，而是注册为后台任务继续运行。
配合 process 工具（实时监控进度）和 kill_process 工具（手动终止）使用。

生命周期：
  terminal 超时注册 → process 监控（实时输出滚动）
    ├─ 任务完成     → 返回全部输出
    ├─ 用户 Ctrl+C  → 停止监控，任务继续后台运行
    ├─ 运行满 1 小时 → process 自动 kill
    └─ 用户/AI 要求  → kill_process 手动终止
"""
import threading
import time
from typing import Optional


class BackgroundTask:
    """一个后台运行的任务"""

    def __init__(self, task_id: int, command: str, process,
                 out_chunks: list, err_chunks: list):
        self.task_id = task_id
        self.command = command
        self.process = process          # subprocess.Popen
        self.start_time = time.time()
        # 输出缓冲（读线程持续 append，本对象只读；list.append 在 GIL 下原子安全）
        self.out_chunks = out_chunks
        self.err_chunks = err_chunks
        self.killed = False             # 是否被 kill_process 终止

    @property
    def done(self) -> bool:
        """主进程是否已退出"""
        return self.process.poll() is not None

    @property
    def returncode(self) -> Optional[int]:
        return self.process.poll()

    @property
    def elapsed(self) -> float:
        """已运行秒数"""
        return time.time() - self.start_time

    def full_output(self) -> str:
        """当前全部输出（stdout + stderr 合并解码）"""
        out = b''.join(self.out_chunks).decode('utf-8', errors='replace')
        err = b''.join(self.err_chunks).decode('utf-8', errors='replace')
        return out + err

    def elapsed_str(self) -> str:
        """可读的运行时长，如 2m35s"""
        s = int(self.elapsed)
        if s < 60:
            return f"{s}s"
        m, s = divmod(s, 60)
        if m < 60:
            return f"{m}m{s:02d}s"
        h, m = divmod(m, 60)
        return f"{h}h{m:02d}m"


class ProcessManager:
    """后台任务注册表（全局单例）"""

    def __init__(self):
        self._tasks: dict[int, BackgroundTask] = {}
        self._next_id = 1
        self._lock = threading.Lock()

    def register(self, command: str, process,
                 out_chunks: list, err_chunks: list) -> BackgroundTask:
        """注册一个后台任务，返回任务对象（含 task_id）"""
        with self._lock:
            task = BackgroundTask(
                self._next_id, command, process, out_chunks, err_chunks
            )
            self._tasks[task.task_id] = task
            self._next_id += 1
        return task

    def get(self, task_id: int) -> Optional[BackgroundTask]:
        with self._lock:
            return self._tasks.get(task_id)

    def list(self) -> list:
        """所有任务（按 task_id 排序）"""
        with self._lock:
            return [self._tasks[k] for k in sorted(self._tasks)]

    def list_running(self) -> list:
        return [t for t in self.list() if not t.done]

    def remove(self, task_id: int):
        with self._lock:
            self._tasks.pop(task_id, None)

    def cleanup_finished(self, keep: int = 20):
        """清理已完成的任务（保留最近 keep 个，避免无限增长）"""
        with self._lock:
            done_ids = [k for k, t in self._tasks.items() if t.done]
            done_ids.sort()
            for k in done_ids[:-keep]:
                self._tasks.pop(k, None)


# 全局单例
_manager = ProcessManager()


def get_process_manager() -> ProcessManager:
    """获取全局进程管理器单例"""
    return _manager
