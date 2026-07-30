"""
PyInstaller runtime hook — 在应用启动前执行

功能:
1. 探测系统 Python 的 site-packages 并注入 sys.path
   → 用户 pip install pandas 后，cbhpacks 工具自动可用
2. 对未安装的包（pandas/joblib）注入假模块占位，避免启动崩溃
   → 用户使用 cbhpacks 工具时会报 AttributeError 提示安装
3. 设置 TIKTOKEN_CACHE_DIR 指向打包的 BPE 缓存目录
4. 设置 MPLBACKEND 避免 matplotlib 在无 GUI 环境报错
"""
import os
import sys
import types
import shutil
import subprocess

# ---------------------------------------------------------------------------
# 1. 探测系统 Python 的 site-packages 并注入 sys.path
#    这样用户 pip install 的包（如 pandas）可以被 cbhpacks 工具使用
# ---------------------------------------------------------------------------
_meipass = getattr(sys, '_MEIPASS', None)

if _meipass:
    # --- 探测系统 Python 可执行文件 ---
    _system_python = None

    # 优先级 1: 环境变量 CBHCLI_PYTHON
    _env_py = os.environ.get('CBHCLI_PYTHON')
    if _env_py and os.path.isfile(_env_py):
        _system_python = _env_py

    # 优先级 2: PATH 搜索 python3 / python
    if not _system_python:
        for _name in ('python3', 'python'):
            _found = shutil.which(_name)
            if _found:
                # 排除 PyInstaller 自身解压目录中的 python
                if not os.path.abspath(_found).startswith(_meipass):
                    _system_python = _found
                    break

    # 优先级 3: 常见路径硬探测
    if not _system_python:
        for _path in ('/usr/bin/python3', '/usr/local/bin/python3', '/usr/bin/python'):
            if os.path.isfile(_path):
                _system_python = _path
                break

    # --- 获取系统 Python 的 site-packages 路径 ---
    if _system_python:
        try:
            _result = subprocess.run(
                [_system_python, '-c',
                 'import site, sys; '
                 'paths = site.getsitepackages() + [site.getusersitepackages()]; '
                 'print("\\n".join(paths))'],
                capture_output=True, text=True, timeout=5
            )
            if _result.returncode == 0:
                for _p in _result.stdout.strip().split('\n'):
                    _p = _p.strip()
                    if _p and os.path.isdir(_p) and _p not in sys.path:
                        sys.path.append(_p)
        except Exception:
            pass

# ---------------------------------------------------------------------------
# 2. 对未安装的包注入假模块占位
#    如果系统 Python 已安装 pandas，上面的 site-packages 注入会让真 pandas 可用
#    如果没装，这里注入假模块避免 import 崩溃，用户使用 cbhpacks 时会报错提示
# ---------------------------------------------------------------------------
for _name in ('pandas', 'joblib'):
    # 先尝试真实导入（可能通过系统 site-packages 已可用）
    try:
        __import__(_name)
    except ImportError:
        if _name not in sys.modules:
            sys.modules[_name] = types.ModuleType(_name)

# ---------------------------------------------------------------------------
# 3. tiktoken BPE 缓存目录
# ---------------------------------------------------------------------------
if _meipass:
    _tiktoken_cache = os.path.join(_meipass, 'tiktoken_cache')
    if os.path.isdir(_tiktoken_cache):
        os.environ['TIKTOKEN_CACHE_DIR'] = _tiktoken_cache

# ---------------------------------------------------------------------------
# 4. matplotlib 无 GUI 后端
# ---------------------------------------------------------------------------
os.environ.setdefault('MPLBACKEND', 'Agg')

# ---------------------------------------------------------------------------
# 5. 禁用 chromadb 遥测（注入假 posthog 模块）
#    背景: chromadb 0.6.3 调用 posthog.capture(user_id, event, properties)
#    3 个位置参数，与 posthog 6.x 新签名 capture(event, **kwargs) 不兼容，
#    导致每次启动打印 "Failed to send telemetry event ClientStartEvent"。
#    cbhcli 本地 CLI 无需遥测，直接用 no-op 假模块替换，一劳永逸。
# ---------------------------------------------------------------------------
class _NoopPosthog(types.ModuleType):
    """任意属性访问都返回 no-op 函数，兼容 posthog 的所有调用方式"""

    def __getattr__(self, _name):
        return self._noop

    @staticmethod
    def _noop(*_args, **_kwargs):
        return None


_fake_posthog = _NoopPosthog('posthog')
_fake_posthog.disabled = True
sys.modules['posthog'] = _fake_posthog