"""cbhcli-admin 管理服务端包（v5.4.0）

内网用户认证与审计管理服务：
- server.py     FastAPI 应用构建 + run_admin() 启动入口（含启动密码门）
- master.py     管理员主密码校验（内置默认 + 环境变量 + admin.json）
- store.py      SQLite 存储层（users/tokens/sessions/events/logins）
- live.py       内存实时注册表（online_users / running_sessions）
- auth_api.py   面向客户端的认证 API（login/validate/heartbeat/logout/ping）
- report_api.py 审计上报接收（batch/snapshot）
- admin_api.py  管理页面 API（用户/会话/事件/设置/导出）
- static/       管理页面（原生 JS SPA，零构建）
"""
from pathlib import Path
from typing import Optional

_DEFAULT_DATA_DIR = Path.home() / ".cbhcli-admin"
_data_dir: Optional[Path] = None


def set_data_dir(p) -> None:
    """覆盖数据目录（--data-dir 参数）。"""
    global _data_dir
    _data_dir = Path(p).expanduser() if p else None


def data_dir() -> Path:
    """服务端数据目录（默认 ~/.cbhcli-admin；自动创建）。"""
    d = _data_dir or _DEFAULT_DATA_DIR
    try:
        d.mkdir(parents=True, exist_ok=True)
        import os
        os.chmod(str(d), 0o700)
    except Exception:
        pass
    return d


def snapshot_dir() -> Path:
    d = data_dir() / "snapshots"
    try:
        d.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    return d
