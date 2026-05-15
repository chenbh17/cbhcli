"""会话历史管理 - 保存和恢复会话记录"""
import json
from pathlib import Path
from datetime import datetime
from typing import Optional


class SessionHistoryManager:
    """管理会话历史的保存和恢复
    
    会话文件保存在 agent 工作空间目录下的 history/ 文件夹中。
    每次 /new 或 /reset 时自动保存当前会话。
    用户可以使用 /resume 命令恢复之前的会话。
    """
    
    def __init__(self, agent_workspace: Path):
        """
        Args:
            agent_workspace: Agent 工作空间目录
        """
        self.history_dir = agent_workspace / "history"
        self.history_dir.mkdir(parents=True, exist_ok=True)
    
    def save_session(self, messages: list[dict], session_id: str = "") -> str:
        """保存会话到 history 文件夹
        
        Args:
            messages: 会话消息列表（API 格式）
            session_id: 会话ID，如果为空则自动生成
            
        Returns:
            保存的文件路径
        """
        if not messages:
            return ""
        
        # 生成文件名：时间戳_会话ID
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        if not session_id:
            session_id = datetime.now().strftime("%H%M%S")
        
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
        
        # 保存会话数据
        session_data = {
            "id": session_id,
            "created_at": datetime.now().isoformat(),
            "title": first_user_msg or "空会话",
            "message_count": len(cleaned_messages),
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
                    "title": data.get("title", ""),
                    "created_at": data.get("created_at", ""),
                    "message_count": data.get("message_count", 0)
                })
            except (json.JSONDecodeError, KeyError):
                continue
            
            if len(sessions) >= limit:
                break
        
        return sessions
    
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
