"""QQ Bot 用户注册表 - 持久化 openid 记录

背景:
    QQ 官方的 openid 是每个用户对每个 Bot 唯一的加密标识，无法从 QQ 号推算。
    此前 openid 只存在 message_handler._contexts 内存字典中，进程重启即丢失，
    其他进程/Agent 也拿不到，导致"只有用户先发消息后工具才能回复"。

    本模块把所有与 Bot 交互过的用户 openid 持久化到 ~/.cbhcli/qqbot_registry.json，
    任何进程的任何 Agent 都能:
    - 按昵称模糊查找用户 openid
    - 直接用 openid 主动发消息（不需要用户先发消息）

    发送消息走 REST API（只需 appId/appSecret 获取 access_token），
    不依赖 WebSocket 网关在线。

多进程安全:
    每次写入前重新读盘合并（读-合并-写），尽力而为合并多个进程的记录。
    写入失败静默（绝不影响消息主流程）。
"""
import json
import logging
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class QQBotUserRegistry:
    """QQ Bot 用户注册表（持久化 openid 记录）"""

    def __init__(self, path: Optional[Path] = None):
        self.path = path or (Path.home() / ".cbhcli" / "qqbot_registry.json")

    # ── 底层读写 ──

    def _load(self) -> dict:
        """读取注册表（损坏/不存在时返回空结构）"""
        try:
            if self.path.exists():
                with open(self.path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    data.setdefault("c2c", {})
                    data.setdefault("group", {})
                    return data
        except Exception as e:
            logger.debug(f"读取注册表失败，将重建: {e}")
        return {"c2c": {}, "group": {}}

    def _save(self, data: dict):
        """写回注册表（失败静默）"""
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.debug(f"写入注册表失败: {e}")

    # ── 记录（消息事件时调用）──

    def record_user(
        self,
        message_type: str,
        openid: str,
        name: str = "",
        bot: str = "",
        content: str = "",
        author_name: str = "",
        author_id: str = "",
    ):
        """记录一个发送目标（用户/群）

        c2c 场景: openid = 用户 openid
        group 场景: openid = group_openid（发送目标），同时记录最近发言人帮助识别

        读-合并-写：每次先读盘拿其他进程的最新记录再合并，尽力多进程安全。
        """
        if not openid:
            return
        try:
            data = self._load()
            entry = data[message_type].get(openid, {})
            if name:
                entry["name"] = name
            entry.setdefault("name", "")
            if bot:
                entry["bot"] = bot
            entry["last_active"] = time.time()
            entry["last_msg"] = (content or "")[:100]
            if author_name:
                entry["last_author_name"] = author_name
            if author_id:
                entry["last_author_id"] = author_id
            data[message_type][openid] = entry
            self._save(data)
        except Exception as e:
            logger.debug(f"记录用户失败: {e}")

    # ── 查询 ──

    def find_by_name(self, keyword: str, message_type: Optional[str] = None) -> list[dict]:
        """按昵称模糊查找（包含匹配，大小写不敏感）

        Returns:
            [{"openid":..., "name":..., "message_type":..., "bot":..., "last_active":...}, ...]
            按最后活跃时间倒序
        """
        results = []
        keyword = (keyword or "").strip().lower()
        if not keyword:
            return results
        data = self._load()
        types = [message_type] if message_type in ("c2c", "group") else ["c2c", "group"]
        for t in types:
            for openid, entry in data.get(t, {}).items():
                name = entry.get("name", "")
                if keyword in name.lower():
                    item = dict(entry)
                    item["openid"] = openid
                    item["message_type"] = t
                    results.append(item)
        results.sort(key=lambda x: x.get("last_active", 0), reverse=True)
        return results

    def list_all(self, message_type: Optional[str] = None) -> dict:
        """列出全部已知发送目标"""
        data = self._load()
        if message_type in ("c2c", "group"):
            return {message_type: data.get(message_type, {})}
        return data

    def get_recent(self, message_type: str = "c2c") -> Optional[dict]:
        """获取最近活跃的一个发送目标"""
        data = self._load()
        entries = data.get(message_type, {})
        if not entries:
            return None
        best = max(
            entries.items(),
            key=lambda kv: kv[1].get("last_active", 0)
        )
        item = dict(best[1])
        item["openid"] = best[0]
        return item

    def format_list(self) -> str:
        """格式化全部已知用户（供 AI 阅读）"""
        data = self._load()
        lines = []
        for t in ("c2c", "group"):
            label = "私聊用户 (c2c)" if t == "c2c" else "群 (group)"
            entries = data.get(t, {})
            lines.append(f"=== {label} 共 {len(entries)} 个 ===")
            for openid, e in sorted(
                entries.items(), key=lambda kv: kv[1].get("last_active", 0), reverse=True
            ):
                ts = e.get("last_active", 0)
                time_str = time.strftime("%Y-%m-%d %H:%M", time.localtime(ts)) if ts else "?"
                name = e.get("name", "") or "(未知昵称)"
                bot = e.get("bot", "?")
                last_msg = e.get("last_msg", "")
                extra = f" | 最近消息: {last_msg}" if last_msg else ""
                lines.append(
                    f"  openid: {openid} | 昵称: {name} | Bot: {bot} | 最后活跃: {time_str}{extra}"
                )
            lines.append("")
        return "\n".join(lines)