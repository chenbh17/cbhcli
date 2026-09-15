"""审计事件构造 + 权限快照采集（客户端）

事件类型全集见设计文档 7.2；`tracer.` 前缀来自 Tracer 镜像，
其余来自 auth/状态通道。
"""
from __future__ import annotations

import json
import os
import time
import uuid
from typing import Optional

# ===================================================================
#  事件类型常量
# ===================================================================

EV_LOGIN = "auth.login"
EV_LOGOUT = "auth.logout"
EV_VALIDATE_FAILED = "auth.validate_failed"
EV_OFFLINE = "auth.offline"
EV_ROUND_START = "session_round_start"
EV_ROUND_COMPLETE = "session_round_complete"
EV_SESSION_END = "session_end"
EV_REPORT_DROPPED = "report_dropped"
EV_EXEC_START = "exec_start"
EV_EXEC_END = "exec_end"

# 单条 payload 上限（服务端约束 32KB；客户端留余量）
PAYLOAD_MAX_BYTES = 30 * 1024
# prompt / 文本字段截断
PROMPT_TRUNC = 2000
SUMMARY_TRUNC = 200


def new_eid() -> str:
    return "e_" + uuid.uuid4().hex


def make_event(type_: str, session_id: str = "", payload: Optional[dict] = None,
               truncate: bool = True) -> dict:
    """构造一条审计事件（eid 幂等 / ts 客户端时间戳）。"""
    ev = {
        "eid": new_eid(),
        "ts": time.time(),
        "type": type_ or "unknown",
        "session_id": session_id or "",
        "payload": payload or {},
    }
    if truncate:
        ev["payload"] = truncate_payload(ev["payload"])
    return ev


def truncate_payload(payload: dict, level: str = "full") -> dict:
    """裁剪 payload：单条超限截断（level=summary 时进一步脱敏）。"""
    if not isinstance(payload, dict):
        return {"value": str(payload)[:PROMPT_TRUNC]}

    def _cut(v, limit: int):
        if isinstance(v, str) and len(v) > limit:
            return v[:limit] + f"...[截断 {len(v)} 字符]"
        return v

    out = {}
    for k, v in payload.items():
        if k in ("prompt", "message", "arguments", "content", "result", "output",
                 "detail", "error"):
            out[k] = _cut(v, PROMPT_TRUNC)
        else:
            out[k] = v
    # 超限整体压缩：保留关键字段 + 截断 JSON
    try:
        raw = json.dumps(out, ensure_ascii=False)
    except Exception:
        return {"error": "payload 序列化失败"}
    if len(raw.encode("utf-8")) > PAYLOAD_MAX_BYTES:
        raw = raw[:PAYLOAD_MAX_BYTES]
        return {"_truncated": raw}
    return out


# ===================================================================
#  权限快照采集（管理端"用户实际权限"数据源）
# ===================================================================

def _read_permissions_config() -> dict:
    """读 ~/.cbhcli/permissions.json（失败给默认）。"""
    default = {"default_mode": "standard", "yolo_keep_deny": False,
               "rules": {"allow": [], "ask": [], "deny": []}}
    try:
        from cbhcli_pkg.core.permissions import _CONFIG_FILE
        p = _CONFIG_FILE
    except Exception:
        p = None
    if p is None:
        return default
    try:
        with open(p, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        if not isinstance(cfg, dict):
            return default
        rules = cfg.get("rules") or {}
        return {
            "default_mode": cfg.get("default_mode", "standard"),
            "yolo_keep_deny": bool(cfg.get("yolo_keep_deny", False)),
            "rules": {
                "allow": list(rules.get("allow", []) or []),
                "ask": list(rules.get("ask", []) or []),
                "deny": list(rules.get("deny", []) or []),
            },
        }
    except Exception:
        return default


def collect_permissions(agent_manager=None, global_config=None,
                        current_agent: Optional[str] = None,
                        mode: Optional[str] = None) -> dict:
    """采集"用户实际权限"快照（心跳携带；全部容错，失败给空结构）。"""
    cfg = _read_permissions_config()
    mode = mode or cfg.get("default_mode", "standard")

    agents_info = []
    active_agent = current_agent or ""
    try:
        if agent_manager is None:
            from cbhcli_pkg.core.agent import AgentManager
            from pathlib import Path
            workspace_base = Path.home() / ".cbhcli" / "agents"
            try:
                if global_config is None:
                    from cbhcli_pkg.config.global_config import GlobalConfig
                    global_config = GlobalConfig()
                workspace_base = Path(global_config.get_settings().get(
                    "workspace_base", str(workspace_base)))
            except Exception:
                pass
            agent_manager = AgentManager(workspace_base)
    except Exception:
        agent_manager = None

    if global_config is None:
        try:
            from cbhcli_pkg.config.global_config import GlobalConfig
            global_config = GlobalConfig()
        except Exception:
            global_config = None

    try:
        if not active_agent and global_config is not None:
            active_agent = global_config.get_active_agent() or ""
    except Exception:
        pass

    try:
        if agent_manager is not None:
            for c in agent_manager.list_agents():
                name = getattr(c, "name", "") or ""
                if not name:
                    continue
                chain = None
                try:
                    if global_config is not None:
                        chain = global_config.get_active_chain(name)
                except Exception:
                    pass
                agents_info.append({
                    "name": name,
                    "disabled_tools": list(getattr(c, "disabled_tools", []) or []),
                    "active_chain": chain,
                })
    except Exception:
        pass

    return {
        "permission_mode": mode,
        "yolo_keep_deny": bool(cfg.get("yolo_keep_deny", False)),
        "rules": cfg.get("rules", {"allow": [], "ask": [], "deny": []}),
        "active_agent": active_agent or "main",
        "agents": agents_info,
        "surfaces": [],
    }
