"""会话历史管理 - 保存和恢复会话记录"""
import json
import threading
from pathlib import Path
from datetime import datetime
from typing import Optional


# ===================================================================
#  会话元数据缓存（v5.3.3）
# ===================================================================
# 问题：list_sessions() 过去对每个历史文件做完整 json.load()，会话多时
# （几百个文件、上百 MB）侧边栏刷新 / CLI /resume 每次都要解析全部消息
# 内容，实测 cbhcli agent（500 个文件）单次 0.45s 且随会话增长持续变慢。
# 方案：① 元数据字段在 JSON 开头（messages 之前），只读文件头部 8KB 即可
#         解析出 id/title/时间/消息数/工作空间，不必解析整个 messages；
#       ② 按 (mtime_ns, size) 缓存元数据，文件未变则零解析。
_META_CACHE: dict = {}          # filepath(str) -> (mtime_ns, size, meta)
_META_CACHE_LOCK = threading.Lock()
_META_CACHE_MAX = 3000
_META_HEAD_BYTES = 8192


def _normalize_meta(data: dict) -> dict:
    """从会话数据中提取列表展示所需的元数据字段"""
    return {
        "id": data.get("id", "") or "",
        # 压缩标题中的换行/连续空白，避免列表显示折行
        # 后行首出现"1、"等字样被误认为列表编号
        "title": " ".join((data.get("title", "") or "").split()),
        "created_at": data.get("created_at", "") or "",
        # 最后对话时间（每次保存刷新；旧文件无此字段回落创建时间）
        "updated_at": (data.get("updated_at") or data.get("created_at", "") or ""),
        "message_count": data.get("message_count", 0) or 0,
        "workspace": data.get("workspace", "") or "",
    }


def _meta_from_head(filepath: Path) -> Optional[dict]:
    """只读文件头部解析会话元数据（不解析 messages 数组）

    依赖 save_session 的写入格式（json.dump indent=2，元数据字段在
    messages 之前）；格式不符时返回 None，由调用方回退到完整解析。
    """
    try:
        with open(filepath, "rb") as f:
            head = f.read(_META_HEAD_BYTES)
    except OSError:
        return None
    text = head.decode("utf-8", errors="ignore")
    meta: dict = {}
    for line in text.split("\n"):
        s = line.strip()
        if not s:
            continue
        if s == "{":
            continue
        if s.startswith('"messages"'):
            break
        if not s.startswith('"'):
            return None
        s = s.rstrip(",")
        key_part, sep, value_part = s.partition('": ')
        if not sep:
            return None
        try:
            meta[key_part[1:]] = json.loads(value_part)
        except (json.JSONDecodeError, ValueError):
            return None
    if "message_count" not in meta and "id" not in meta:
        return None
    return _normalize_meta(meta)


def _read_session_meta(filepath: Path) -> Optional[dict]:
    """读取（带缓存的）会话元数据"""
    try:
        st = filepath.stat()
    except OSError:
        return None
    sig = (st.st_mtime_ns, st.st_size)
    key = str(filepath)
    with _META_CACHE_LOCK:
        hit = _META_CACHE.get(key)
    if hit is not None and hit[0] == sig[0] and hit[1] == sig[1]:
        return hit[2]

    meta = _meta_from_head(filepath)
    if meta is None:
        # 回退：完整解析（兼容手工编辑/非标准格式的历史文件）
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            return None
        meta = _normalize_meta(data)

    with _META_CACHE_LOCK:
        _META_CACHE[key] = (sig[0], sig[1], meta)
        if len(_META_CACHE) > _META_CACHE_MAX:
            overflow = list(_META_CACHE.keys())[:len(_META_CACHE) - _META_CACHE_MAX]
            for k in overflow:
                _META_CACHE.pop(k, None)
    return meta


def _cache_meta_after_save(filepath: Path, meta: dict) -> None:
    """保存/重命名后立即刷新元数据缓存（省去下次列表的解析）"""
    try:
        st = filepath.stat()
    except OSError:
        return
    with _META_CACHE_LOCK:
        _META_CACHE[str(filepath)] = (st.st_mtime_ns, st.st_size, dict(meta))


def _drop_meta_cache(filepath: Path) -> None:
    with _META_CACHE_LOCK:
        _META_CACHE.pop(str(filepath), None)


def build_session_data(messages: list, session_id: str = "",
                       workspace: str = "", title: str = "",
                       created_at: str = "", existing_title: str = "") -> dict:
    """构造会话 JSON（与 save_session 落盘结构完全一致）

    v5.4.0 从 save_session 中提取（纯重构，行为不变）：供审计快照上报
    在无本地文件路径（如 exec --no-save）时直接构造上传数据。

    Args:
        messages: 会话消息列表（API 格式）
        session_id: 会话ID（为空自动生成）
        workspace: 所属工作空间目录
        title: 显式标题（优先）
        created_at: 创建时间（为空用当前时间；保存已有会话时传原值）
        existing_title: 已有文件的标题（次优先）
    """
    if not session_id:
        session_id = datetime.now().strftime("%H%M%S")

    # 提取用户输入作为会话标题（处理多模态content格式）
    first_user_msg = ""
    for msg in messages or []:
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
    for msg in messages or []:
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
    # v5.3.1+：updated_at 每次保存刷新（最后对话时间），created_at 保持首次创建时间
    return {
        "id": session_id,
        "created_at": created_at or datetime.now().isoformat(),
        "updated_at": datetime.now().isoformat(),
        "title": " ".join((title or "").split())
                 or " ".join((existing_title or "").split())
                 or " ".join((first_user_msg or "").split())
                 or "空会话",
        "message_count": len(cleaned_messages),
        "workspace": workspace or "",
        "messages": cleaned_messages
    }


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

        # 构造会话数据（v5.4.0 提取为模块级 build_session_data，行为不变）
        session_data = build_session_data(
            messages, session_id=session_id, workspace=workspace, title=title,
            created_at=created_at, existing_title=existing_title)

        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(session_data, f, ensure_ascii=False, indent=2)
        _cache_meta_after_save(filepath, _normalize_meta(session_data))
        
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
            meta = _read_session_meta(filepath)
            if meta is None:
                continue
            entry = dict(meta)
            entry["filename"] = filepath.name
            entry["filepath"] = str(filepath)
            sessions.append(entry)
            
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

    def get_meta(self, filename: str) -> Optional[dict]:
        """单会话元数据（带缓存；只读文件头 8KB，不解析 messages）。

        v5.4.0+：供服务端在完整加载会话前做轻量判断（如作用域预检），
        避免"解析完大会话才发现被拒"的浪费（逐个尝试恢复会话场景下
        每个被拒会话省 10~30ms）。非标准格式时返回 None（调用方回退）。
        """
        if not filename or "/" in filename or "\\" in filename:
            return None
        return _read_session_meta(self.history_dir / filename)

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
            _cache_meta_after_save(filepath, _normalize_meta(data))
            return True
        except (json.JSONDecodeError, OSError):
            _drop_meta_cache(filepath)
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
        _drop_meta_cache(filepath)
        return True
