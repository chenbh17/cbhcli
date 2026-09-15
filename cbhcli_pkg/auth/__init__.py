"""cbhcli 用户认证与审计上报客户端包（v5.4.0）

四端（CLI / Web / Jupyter / exec）共用：
- 凭据存储:  ~/.cbhcli/auth.json（0600，设备级 token）
- 统一拦截:  require_login(surface)
- 审计上报:  get_reporter()（事件流 / 心跳状态流 / 会话快照）
- 登录入口:  login_cmd（shell 命令）+ Web 代理端点
"""
from cbhcli_pkg.auth.store import (AuthStore, auth_status, is_logged_in_fast,
                                   load_device_id)
from cbhcli_pkg.auth.client import (AuthClient, AuthError, normalize_server,
                                    ping_server, PROTOCOL)
from cbhcli_pkg.auth.guard import (require_login, require_login_or_hint,
                                   get_last_reason)
from cbhcli_pkg.auth.reporter import (get_reporter, report_snapshot_file,
                                      report_event, report_stream_text,
                                      flush_stream_text)
from cbhcli_pkg.auth.guide import login_hint_text, expired_hint_text, error_guide

__all__ = [
    "AuthStore", "auth_status", "is_logged_in_fast", "load_device_id",
    "AuthClient", "AuthError", "normalize_server", "ping_server", "PROTOCOL",
    "require_login", "require_login_or_hint", "get_last_reason",
    "get_reporter", "report_snapshot_file", "report_event",
    "report_stream_text", "flush_stream_text",
    "login_hint_text", "expired_hint_text", "error_guide",
]
