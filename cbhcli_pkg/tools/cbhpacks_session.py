"""cbhpacks 工具会话状态管理

与 python 工具共享 PythonSession 命名空间：
- cbhpacks 实例缓存存于会话命名空间的 '_cbhpacks_cache' 中（按工具名隔离）
- 每次执行的结果变量通过 _expose() 注入会话，python 工具可直接使用
- /new 或 /reset 时 remove_python_session() 销毁会话，所有缓存与变量自动释放
"""
from cbhcli_pkg.tools.python_tool import get_python_session
from cbhcli_pkg.tools.registry import BaseTool


class CbhpacksSessionTool(BaseTool):
    """cbhpacks 工具基类 - 会话级状态缓存（与 python 工具共享命名空间）"""

    def __init__(self, session_id: str = "default"):
        self._session_id = session_id

    def set_session_id(self, session_id: str):
        """设置会话ID（与 PythonTool 保持一致）"""
        self._session_id = session_id

    @property
    def _session_globals(self) -> dict:
        """当前会话的全局命名空间"""
        return get_python_session(self._session_id)._globals

    def _get_cache(self, tool_key: str) -> dict:
        """获取本工具的会话级实例缓存（按工具名隔离，随会话销毁自动释放）"""
        cache = self._session_globals.setdefault('_cbhpacks_cache', {})
        return cache.setdefault(tool_key, {})

    def _expose(self, **variables):
        """把结果变量注入 python 会话命名空间，python 工具中可直接使用"""
        self._session_globals.update(variables)
