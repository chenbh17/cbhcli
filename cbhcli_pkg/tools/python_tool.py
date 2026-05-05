"""Python执行工具 - 带会话变量记忆"""
import sys
import os
import io
import shutil
import subprocess
from cbhcli_pkg.tools.registry import BaseTool, ToolResult


# ---------------------------------------------------------------------------
#  PyInstaller 环境下自动探测系统 Python 并注入 site-packages
# ---------------------------------------------------------------------------

_system_paths_injected = False  # 全局只执行一次


def _is_pyinstaller() -> bool:
    """判断是否在 PyInstaller 打包环境中运行"""
    return getattr(sys, '_MEIPASS', None) is not None


def _detect_system_python():
    """自动探测系统 Python 可执行文件路径

    优先级:
    1. 环境变量 CBHCLI_PYTHON
    2. shutil.which("python3")
    3. shutil.which("python")
    4. 常见路径硬探测

    Returns:
        Python 可执行文件路径，或 None
    """
    # 1. 环境变量
    env_python = os.environ.get("CBHCLI_PYTHON")
    if env_python and os.path.isfile(env_python):
        return env_python

    # 2/3. PATH 自动搜索
    meipass = getattr(sys, '_MEIPASS', '')
    for name in ("python3", "python"):
        found = shutil.which(name)
        if found:
            # 排除 PyInstaller 解压目录中的自身
            if meipass and os.path.abspath(found).startswith(meipass):
                continue
            return found

    # 4. 常见路径硬探测
    for path in (
        "/usr/bin/python3",
        "/usr/local/bin/python3",
        "/usr/bin/python",
    ):
        if os.path.isfile(path):
            return path

    return None


def _inject_system_site_packages():
    """将系统 Python 的 site-packages 注入当前进程的 sys.path

    仅在 PyInstaller 环境下执行，且只执行一次。
    """
    global _system_paths_injected
    if _system_paths_injected:
        return
    _system_paths_injected = True

    if not _is_pyinstaller():
        return

    python_bin = _detect_system_python()
    if not python_bin:
        return

    try:
        result = subprocess.run(
            [python_bin, '-c',
             'import site, sys; '
             'paths = site.getsitepackages() + [site.getusersitepackages()]; '
             'print("\\n".join(paths))'],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            for p in result.stdout.strip().split('\n'):
                p = p.strip()
                if p and os.path.isdir(p) and p not in sys.path:
                    sys.path.append(p)
    except Exception:
        pass


# ---------------------------------------------------------------------------
#  Python 会话
# ---------------------------------------------------------------------------

class PythonSession:
    """Python解释器会话 - 保持变量状态"""

    def __init__(self):
        """初始化Python会话"""
        self._globals = {}
        self._init_builtins()

    def _init_builtins(self):
        """初始化内置模块和常用库"""
        # PyInstaller 环境：注入系统 site-packages（仅首次）
        _inject_system_site_packages()

        # 导入常用模块
        for mod_name in ('math', 'json', 'os', 'sys', 're', 'datetime'):
            try:
                mod = __import__(mod_name)
                self._globals[mod_name] = mod
            except ImportError:
                pass

    def reset(self):
        """重置会话（清空所有变量）"""
        self._globals.clear()
        self._init_builtins()

    def execute(self, code: str, timeout: int = 30) -> tuple:
        """
        执行Python代码

        Args:
            code: Python代码
            timeout: 超时时间（秒）- 暂未实现

        Returns:
            (success, output, error)
        """
        # 捕获 stdout 和 stderr
        old_stdout = sys.stdout
        old_stderr = sys.stderr

        stdout_capture = io.StringIO()
        stderr_capture = io.StringIO()

        try:
            sys.stdout = stdout_capture
            sys.stderr = stderr_capture

            # 编译并执行代码
            compiled_code = compile(code, '<python_tool>', 'exec')
            exec(compiled_code, self._globals)

            # 获取输出
            output = stdout_capture.getvalue()
            error = stderr_capture.getvalue()

            success = not error
            return (success, output, error)

        except Exception as e:
            error = stderr_capture.getvalue()
            error += f"\n异常: {type(e).__name__}: {str(e)}"
            return (False, stdout_capture.getvalue(), error)

        finally:
            # 恢复 stdout 和 stderr
            sys.stdout = old_stdout
            sys.stderr = old_stderr


# 全局会话管理器 - 每个应用实例一个
_session_store = {}


def get_python_session(session_id: str) -> PythonSession:
    """获取或创建Python会话"""
    if session_id not in _session_store:
        _session_store[session_id] = PythonSession()
    return _session_store[session_id]


def reset_python_session(session_id: str):
    """重置Python会话"""
    if session_id in _session_store:
        _session_store[session_id].reset()


def remove_python_session(session_id: str):
    """移除Python会话"""
    if session_id in _session_store:
        del _session_store[session_id]


class PythonTool(BaseTool):
    """Python执行工具 - 带会话变量记忆"""

    def __init__(self, session_id: str = "default"):
        """
        初始化Python工具

        Args:
            session_id: 会话ID，用于隔离不同会话的变量
        """
        self._session_id = session_id

    @property
    def name(self) -> str:
        return "python"

    @property
    def description(self) -> str:
        return (
            "执行Python代码。支持导入库、定义变量、执行计算等。\n"
            "**会话记忆**: 同一会话中定义的变量和导入的模块会在后续调用中保留。\n"
            "例如：第一次执行 `import pandas as pd; df = pd.DataFrame(...)` 后，\n"
            "第二次可以直接使用 `pd` 和 `df`。\n"
            "**注意**: 使用 /reset 或 /new 创建新会话后，变量记忆会被清空。"
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "code": {
                    "type": "string",
                    "description": "要执行的Python代码"
                }
            },
            "required": ["code"]
        }

    def set_session_id(self, session_id: str):
        """设置会话ID"""
        self._session_id = session_id

    def execute(self, code: str, timeout: int = 30) -> ToolResult:
        """
        执行Python代码

        Args:
            code: Python代码
            timeout: 超时时间（秒）

        Returns:
            ToolResult: 执行结果
        """
        try:
            # 获取会话
            session = get_python_session(self._session_id)

            # 执行代码
            success, output, error = session.execute(code, timeout)

            # 构建输出
            result_output = ""
            if output:
                result_output += output
            if not result_output:
                result_output = "代码执行成功，无输出"

            return ToolResult(
                success=success,
                output=result_output,
                error=error if error else None
            )

        except Exception as e:
            return ToolResult(
                success=False,
                output="",
                error=f"执行Python代码时出错: {str(e)}"
            )
