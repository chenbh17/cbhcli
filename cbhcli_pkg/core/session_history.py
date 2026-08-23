"""会话历史管理 - 保存和恢复会话记录"""
import json
from pathlib import Path
from datetime import datetime
from typing import Optional


class SessionHistoryManager:
    """管理会话历史的保存和恢复
    
    会话文件保存在 agent 工作空间目录下的 history/ 文件夹中。
    v5.2.6 起每轮对话结束自动保存（同 session_id 幂等覆盖同一文件），
    应用异常退出（崩溃/kill/断网）时最多丢失正在生成的当前轮。
    /new 或 /reset 时保存当前会话；/resume 可恢复历史会话。
    """
    
    def __init__(self, agent_workspace: Path):
        """
        Args:
            agent_workspace: Agent 工作空间目录
        """
        self.history_dir = agent_workspace / "history"
        self.history_dir.mkdir(parents=True, exist_ok=True)
    
    def save_session(self, messages: list[dict], session_id: str = "",
                     workspace: str = "", title: str = "") -> str:
        """保存会话到 history 文件夹（同 session_id 幂等覆盖）

        v5.2.6 起支持每轮对话自动保存：同一 session_id 再次保存时直接
        覆盖更新已有文件（保留首次 created_at），而不是新建文件。
        因此一个会话对应一个文件，/new、quit、每轮自动保存等多次
        调用不会产生重复文件。

        Args:
            messages: 会话消息列表（API 格式）
            session_id: 会话ID，如果为空则自动生成
            workspace: 会话所属工作空间目录（v5.2.8，Web 端按工作空间
                分组展示会话；为空表示默认工作空间）

        Returns:
            保存的文件路径
        """
        if not messages:
            return ""

        if not session_id:
            session_id = datetime.now().strftime("%H%M%S")

        # 幂等定位：查找同 session_id 的已有文件（文件名格式 时间戳_会话ID.json）
        created_at = datetime.now().isoformat()
        existing_title = ""
        filepath = None
        if not any(c in session_id for c in "*?[]"):
            existing = sorted(
                self.history_dir.glob(f"*_{session_id}.json"), reverse=True)
            if existing:
                filepath = existing[0]
                try:
                    with open(filepath, 'r', encoding='utf-8') as f:
                        old_data = json.load(f)
                    # 保留首次保存时间（会话创建时间语义）
                    created_at = old_data.get("created_at", created_at)
                    # 保留已有标题（重命名后的标题不被自动提取标题覆盖）
                    existing_title = old_data.get("title", "") or ""
                except (json.JSONDecodeError, OSError):
                    pass

        if filepath is None:
            # 生成新文件名：时间戳_会话ID
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"{timestamp}_{session_id}.json"
            filepath = self.history_dir / filename
        
        # 提取用户输入作为会话标题（处理多模态content格式）
        first_user_msg = ""
        for msg in messages:
            if msg.get("role") == "user":
                content = msg.get("content", "")
                if isinstance(content, list):
                    # 多模态格式: [{"type": "text", "text": "..."}, {"type": "image_url", ...}]
                    for part in content:
                        if isinstance(part, dict) and part.get("type") == "text":
                            first_user_msg = part.get("text", "")[:50]
                            break
                elif isinstance(content, str):
                    first_user_msg = content[:50]
                break
        
        # 清理消息中的base64图片数据（保存时去除，减小文件体积）
        cleaned_messages = []
        for msg in messages:
            cleaned = dict(msg)
            content = cleaned.get("content", "")
            if isinstance(content, list):
                # 多模态格式：只保留文本部分
                text_parts = [p for p in content if isinstance(p, dict) and p.get("type") == "text"]
                if text_parts:
                    cleaned["content"] = text_parts[0].get("text", "")
                else:
                    cleaned["content"] = ""
            cleaned_messages.append(cleaned)
        
        # 保存会话数据（标题优先级: 显式传入 > 已有文件 > 首条用户消息提取）
        # 标题统一压缩换行/连续空白为单空格（避免列表折行伪装成编号）
        session_data = {
            "id": session_id,
            "created_at": created_at,
            "title": " ".join((title or "").split())
                     or " ".join(existing_title.split())
                     or " ".join((first_user_msg or "").split())
                     or "空会话",
            "message_count": len(cleaned_messages),
            "workspace": workspace or "",
            "messages": cleaned_messages
        }
        
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(session_data, f, ensure_ascii=False, indent=2)
        
        return str(filepath)
    
    def list_sessions(self, limit: int = 20) -> list[dict]:
        """列出所有保存的会话
        
        Args:
            limit: 最多返回数量
            
        Returns:
            会话列表，按时间倒序
        """
        if not self.history_dir.exists():
            return []
        
        sessions = []
        for filepath in sorted(self.history_dir.glob("*.json"), reverse=True):
            try:
                with open(filepath, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                sessions.append({
                    "filename": filepath.name,
                    "filepath": str(filepath),
                    "id": data.get("id", ""),
                    # v5.2.8：压缩标题中的换行/连续空白，避免列表显示折行
                    # 后行首出现"1、"等字样被误认为列表编号
                    "title": " ".join((data.get("title", "") or "").split()),
                    "created_at": data.get("created_at", ""),
                    "message_count": data.get("message_count", 0),
                    "workspace": data.get("workspace", "")
                })
            except (json.JSONDecodeError, KeyError):
                continue
            
            if len(sessions) >= limit:
                break
        
        return sessions
    
    def load_session_full(self, filename: str) -> Optional[dict]:
        """加载会话完整数据（id/created_at/title/workspace/messages）。

        v5.2.8：恢复会话时保留原会话 id/创建时间/工作空间，
        使后续自动保存幂等覆盖同一文件（不再产生副本）。
        """
        filepath = self.history_dir / filename
        if not filepath.exists():
            return None
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (json.JSONDecodeError, KeyError, OSError):
            return None

    def update_session_title(self, filename: str, title: str) -> bool:
        """重命名会话标题（v5.2.8 侧边栏会话管理）。"""
        filepath = self.history_dir / filename
        if not filepath.exists():
            return False
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                data = json.load(f)
            data["title"] = title
            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            return True
        except (json.JSONDecodeError, OSError):
            return False

    def load_session(self, filename: str) -> Optional[list[dict]]:
        """加载会话消息
        
        Args:
            filename: 会话文件名（不含路径）
            
        Returns:
            消息列表，如果加载失败返回 None
        """
        filepath = self.history_dir / filename
        
        if not filepath.exists():
            return None
        
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                data = json.load(f)
            return data.get("messages", [])
        except (json.JSONDecodeError, KeyError):
            return None
    
    def delete_session(self, filename: str) -> bool:
        """删除会话文件
        
        Args:
            filename: 会话文件名
            
        Returns:
            是否删除成功
        """
        filepath = self.history_dir / filename
        
        if not filepath.exists():
            return False
        
        filepath.unlink()
        return True
