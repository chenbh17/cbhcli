"""AuthClient：客户端到管理服务器（cbhcli-admin）的网络层。

- 纯标准库 urllib（对齐 core/web_notify.py，不引入 requests）
- 短超时（默认 3s）+ 静默语义由调用方决定
- 所有请求带 protocol 版本；Accept: application/json
- 服务器地址规范化：IP / IP:PORT / http(s)://IP:PORT

错误模型：
  AuthError(code, message)
    code ∈ unreachable / timeout / http_error / invalid_key / disabled /
           protocol_mismatch / not_logged_in / server_error
"""
from __future__ import annotations

import json
import socket
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Optional

from cbhcli_pkg.auth.store import AuthStore, load_device_id

PROTOCOL = 1
DEFAULT_PORT = 28888
DEFAULT_TIMEOUT = 3.0

# 服务端兼容的主版本（主版本一致即可，5.4.x ↔ 5.4.x）
try:
    from cbhcli_pkg import __version__ as CLIENT_VERSION
except Exception:  # pragma: no cover
    CLIENT_VERSION = "0.0.0"


class AuthError(Exception):
    """认证/网络错误（携带机器可读 code 与用户可读中文说明）"""

    def __init__(self, code: str, message: str = "", detail: str = ""):
        super().__init__(message or code)
        self.code = code
        self.message = message or code
        self.detail = detail

    def __str__(self) -> str:
        return self.message


def normalize_server(text: str) -> str:
    """规范化服务器地址为 http(s)://host:port 形式。

    允许输入：
      - "192.168.1.100"          -> http://192.168.1.100:28888
      - "192.168.1.100:28888"    -> http://192.168.1.100:28888
      - "http://host:28888"      -> 原样（去尾部斜杠）
      - "https://host"           -> https://host:28888
    """
    s = (text or "").strip()
    if not s:
        return ""
    if not s.startswith(("http://", "https://")):
        s = "http://" + s
    parsed = urllib.parse.urlsplit(s)
    scheme = parsed.scheme or "http"
    host = parsed.hostname or ""
    port = parsed.port or DEFAULT_PORT
    if not host:
        return ""
    # IPv6 补方括号
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    return f"{scheme}://{host}:{port}"


def _is_main_version_compatible(client_v: str, server_v: str) -> bool:
    """主版本一致即兼容（5.4.x ↔ 5.4.x）；无法解析时宽松放行。"""
    try:
        c = str(client_v).split("-")[0].split(".")
        s = str(server_v).split("-")[0].split(".")
        return c[0] == s[0] and (len(c) < 2 or len(s) < 2 or c[1] == s[1])
    except Exception:
        return True


class AuthClient:
    """管理服务器 API 客户端（无状态；每次从 auth.json 读 token 亦可显式传入）"""

    def __init__(self, server: str = "", token: str = "", timeout: float = DEFAULT_TIMEOUT):
        self.server = normalize_server(server) if server else ""
        self.token = token or ""
        self.timeout = timeout

    # ---------------------------------------------------------------
    #  低层请求
    # ---------------------------------------------------------------

    def _request(self, method: str, path: str, payload: Optional[dict] = None,
                 server: str = "", token: str = "", timeout: Optional[float] = None,
                 require_token: bool = False) -> dict:
        base = normalize_server(server) if server else self.server
        if not base:
            raise AuthError("unreachable", "未配置服务器地址")
        tok = token or self.token
        if require_token and not tok:
            raise AuthError("not_logged_in", "本机未登录")
        url = base + path
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8") \
            if payload is not None else None
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": f"cbhcli/{CLIENT_VERSION}",
        }
        if tok:
            headers["Authorization"] = f"Bearer {tok}"
        req = urllib.request.Request(url, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout or self.timeout) as resp:
                body = resp.read().decode("utf-8", errors="replace")
            try:
                return json.loads(body) if body else {}
            except (ValueError, TypeError):
                return {"ok": True, "raw": body}
        except urllib.error.HTTPError as e:
            # 服务端返回 JSON 错误体（4xx/5xx）；解析错误码
            code = ""
            msg = ""
            try:
                raw = e.read().decode("utf-8", errors="replace")
                parsed = json.loads(raw) if raw else {}
                if isinstance(parsed, dict):
                    code = parsed.get("error", "") or ""
                    msg = parsed.get("message", "") or ""
            except Exception:
                pass
            if e.code == 401:
                raise AuthError(code or "invalid_key",
                                msg or "认证失败（密钥无效或已失效）")
            if e.code == 413:
                raise AuthError("too_large", msg or "上传内容超过服务端限制")
            if e.code == 429:
                raise AuthError("rate_limited", msg or "请求过于频繁，请稍后再试")
            raise AuthError("http_error",
                            msg or f"服务端返回 HTTP {e.code}", detail=str(e))
        except socket.timeout:
            raise AuthError("timeout", "连接超时（网络不通或防火墙拦截）")
        except urllib.error.URLError as e:
            reason = getattr(e, "reason", None)
            if isinstance(reason, socket.timeout):
                raise AuthError("timeout", "连接超时（网络不通或防火墙拦截）")
            if isinstance(reason, ssl.SSLError):
                raise AuthError("tls_error",
                                "TLS 握手失败（服务端证书不受信任）", detail=str(reason))
            raise AuthError("unreachable",
                            "无法连接服务器（服务未启动或端口错误）", detail=str(reason))
        except ConnectionRefusedError:
            raise AuthError("unreachable", "连接被拒绝（服务未启动或端口错误）")
        except Exception as e:
            raise AuthError("unreachable", f"请求失败: {e}")

    # ---------------------------------------------------------------
    #  公开 API
    # ---------------------------------------------------------------

    def ping(self, server: str = "", timeout: Optional[float] = None) -> dict:
        """GET /api/ping —— 连通性测试（返回延迟/版本/兼容性）。

        Returns:
            {"ok": True, "latency_ms": 3, "server_version": "5.4.1",
             "protocol": 1, "compatible": True, "service": "cbhcli-admin"}
        失败抛 AuthError（调用方转成诊断展示）。
        """
        t0 = time.time()
        data = self._request("GET", "/api/ping", server=server,
                             timeout=timeout or 3.0)
        latency = int((time.time() - t0) * 1000)
        if not isinstance(data, dict) or not data.get("ok", False):
            raise AuthError("http_error", "服务端响应异常（非 cbhcli-admin 服务？）")
        sv = data.get("version", "")
        proto = int(data.get("protocol", 0) or 0)
        compatible = (proto == PROTOCOL) and _is_main_version_compatible(
            CLIENT_VERSION, sv)
        return {
            "ok": True,
            "latency_ms": latency,
            "server_version": sv,
            "protocol": proto,
            "compatible": compatible,
            "service": data.get("service", ""),
        }

    def login(self, server: str, key: str, surface: str = "cli") -> dict:
        """POST /api/auth/login —— 登录换取 token（不落盘，由调用方写 auth.json）。"""
        client = self._client_info(surface)
        payload = {"key": key, "client": client}
        data = self._request("POST", "/api/auth/login", payload=payload,
                             server=server, timeout=max(self.timeout, 5.0))
        if not isinstance(data, dict) or not data.get("ok"):
            raise AuthError(data.get("error", "invalid_key") if isinstance(data, dict)
                            else "invalid_key",
                            (data.get("message") if isinstance(data, dict) else "")
                            or "登录失败")
        return data

    def validate(self, store: Optional[AuthStore] = None,
                 surface: str = "cli") -> dict:
        """POST /api/auth/validate —— 在线复核 token。

        成功：{"ok": True, "user", "policy", "expires_at"(可选 new_token)}
        失败抛 AuthError（401 -> invalid_key/disabled 等；网络 -> unreachable/timeout）
        """
        store = store or AuthStore()
        data = store.load()
        if not data:
            raise AuthError("not_logged_in", "本机未登录")
        payload = {"client": self._client_info(surface)}
        resp = self._request("POST", "/api/auth/validate", payload=payload,
                             server=data.get("server", ""),
                             token=data.get("token", ""),
                             timeout=max(self.timeout, 5.0),
                             require_token=True)
        if not isinstance(resp, dict) or not resp.get("ok"):
            raise AuthError((resp or {}).get("error", "invalid_key"),
                            (resp or {}).get("message", "") or "凭据校验失败")
        return resp

    def heartbeat(self, running_sessions: list, permissions: Optional[dict] = None,
                  client_extra: Optional[dict] = None,
                  store: Optional[AuthStore] = None,
                  timeout: Optional[float] = None,
                  instance_id: str = "") -> dict:
        """POST /api/auth/heartbeat —— 60s 心跳（携带运行中会话 + 权限快照）。

        instance_id：本进程实例标识（服务端按实例隔离运行中会话集合，
        避免多进程心跳互相覆盖）。
        """
        store = store or AuthStore()
        data = store.load()
        if not data:
            raise AuthError("not_logged_in", "本机未登录")
        payload = {
            "running_sessions": running_sessions or [],
            "permissions": permissions or {},
            "client": {**self._client_info("cli"), **(client_extra or {})},
        }
        if instance_id:
            payload["instance_id"] = instance_id
        resp = self._request("POST", "/api/auth/heartbeat", payload=payload,
                             server=data.get("server", ""),
                             token=data.get("token", ""),
                             timeout=timeout or self.timeout,
                             require_token=True)
        if not isinstance(resp, dict) or not resp.get("ok"):
            raise AuthError((resp or {}).get("error", "invalid_key"),
                            (resp or {}).get("message", "") or "心跳失败")
        return resp

    def report_batch(self, events: list,
                     store: Optional[AuthStore] = None,
                     timeout: Optional[float] = None) -> dict:
        """POST /api/report/batch —— 批量上报审计事件（≤100 条/批）。"""
        store = store or AuthStore()
        data = store.load()
        if not data:
            raise AuthError("not_logged_in", "本机未登录")
        resp = self._request("POST", "/api/report/batch",
                             payload={"events": events or []},
                             server=data.get("server", ""),
                             token=data.get("token", ""),
                             timeout=timeout or max(self.timeout, 5.0),
                             require_token=True)
        if not isinstance(resp, dict) or not resp.get("ok"):
            raise AuthError((resp or {}).get("error", "server_error"),
                            (resp or {}).get("message", "") or "上报失败")
        return resp

    def report_snapshot(self, session_id: str, agent: str, surface: str,
                        updated_at: str, message_count: int, data: dict,
                        store: Optional[AuthStore] = None,
                        timeout: Optional[float] = None) -> dict:
        """POST /api/report/snapshot —— 会话快照上报（低频重型）。"""
        store = store or AuthStore()
        cred = store.load()
        if not cred:
            raise AuthError("not_logged_in", "本机未登录")
        payload = {
            "session_id": session_id,
            "agent": agent,
            "surface": surface,
            "updated_at": updated_at,
            "message_count": message_count,
            "data": data,
        }
        resp = self._request("POST", "/api/report/snapshot", payload=payload,
                             server=cred.get("server", ""),
                             token=cred.get("token", ""),
                             timeout=timeout or 10.0,
                             require_token=True)
        if not isinstance(resp, dict) or not resp.get("ok"):
            raise AuthError((resp or {}).get("error", "server_error"),
                            (resp or {}).get("message", "") or "快照上报失败")
        return resp

    def logout(self, store: Optional[AuthStore] = None,
               timeout: Optional[float] = None) -> bool:
        """POST /api/auth/logout —— 尽力而为吊销服务端 token（失败不抛）。"""
        store = store or AuthStore()
        data = store.load()
        if not data:
            return True
        try:
            self._request("POST", "/api/auth/logout", payload={},
                          server=data.get("server", ""),
                          token=data.get("token", ""),
                          timeout=timeout or self.timeout,
                          require_token=True)
            return True
        except Exception:
            return False

    # ---------------------------------------------------------------
    #  客户端信息
    # ---------------------------------------------------------------

    def _client_info(self, surface: str) -> dict:
        hostname = ""
        os_user = ""
        try:
            hostname = socket.gethostname()
        except Exception:
            pass
        try:
            import getpass
            os_user = getpass.getuser()
        except Exception:
            pass
        return {
            "hostname": hostname,
            "os_user": os_user,
            "version": CLIENT_VERSION,
            "surface": surface or "cli",
            "device_id": load_device_id(),
            "protocol": PROTOCOL,
        }


def ping_server(server: str, timeout: float = 3.0) -> dict:
    """模块级便捷函数：测试任意服务器连通性（未登录可用）。"""
    return AuthClient(server=server, timeout=timeout).ping(server=server)
