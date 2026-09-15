"""AdminStore：服务端 SQLite 存储层（线程安全）

表：users / tokens / sessions / events / logins
- 单例（get_store()），sqlite3 + check_same_thread=False + WAL + 写锁
- 主密码/密钥/token 全部哈希存储（sha256+salt / sha256）
- 快照文件：snapshots/<user_id>/<session_id>.json（临时文件 + os.replace 原子写）
- 数据库每日备份 1 份到 backup/（保留 30 份）
"""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import shutil
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from cbhcli_pkg.admin import data_dir, snapshot_dir

_store_singleton: Optional["AdminStore"] = None
_singleton_lock = threading.Lock()

_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  id            TEXT PRIMARY KEY,
  name          TEXT UNIQUE NOT NULL,
  display_name  TEXT DEFAULT '',
  key_hash      TEXT NOT NULL,
  key_salt      TEXT NOT NULL,
  key_prefix    TEXT DEFAULT '',
  key_plain     TEXT DEFAULT '',
  key_version   INTEGER DEFAULT 1,
  token_ttl_days INTEGER,
  created_at    TEXT NOT NULL,
  disabled      INTEGER DEFAULT 0,
  note          TEXT DEFAULT '',
  last_login_at TEXT DEFAULT '',
  last_seen_at  TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS tokens (
  id          TEXT PRIMARY KEY,
  user_id     TEXT NOT NULL,
  token_hash  TEXT NOT NULL,
  created_at  TEXT NOT NULL,
  expires_at  TEXT,
  revoked     INTEGER DEFAULT 0,
  key_version INTEGER DEFAULT 1,
  client_info TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_tokens_hash ON tokens(token_hash);
CREATE INDEX IF NOT EXISTS idx_tokens_user ON tokens(user_id);
CREATE TABLE IF NOT EXISTS sessions (
  user_id       TEXT NOT NULL,
  session_id    TEXT NOT NULL,
  agent         TEXT DEFAULT '',
  workspace     TEXT DEFAULT '',
  title         TEXT DEFAULT '',
  model         TEXT DEFAULT '',
  status        TEXT DEFAULT 'idle',
  created_at    TEXT DEFAULT '',
  updated_at    TEXT DEFAULT '',
  last_heartbeat REAL DEFAULT 0,
  message_count INTEGER DEFAULT 0,
  PRIMARY KEY (user_id, session_id)
);
CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_sessions_status ON sessions(status, updated_at DESC);
CREATE TABLE IF NOT EXISTS events (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  eid        TEXT UNIQUE,
  user_id    TEXT NOT NULL,
  session_id TEXT DEFAULT '',
  ts         REAL NOT NULL,
  recv_ts    REAL NOT NULL,
  type       TEXT NOT NULL,
  payload    TEXT DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_events_ts ON events(recv_ts DESC);
CREATE INDEX IF NOT EXISTS idx_events_user ON events(user_id, recv_ts DESC);
CREATE INDEX IF NOT EXISTS idx_events_type ON events(type, recv_ts DESC);
CREATE TABLE IF NOT EXISTS logins (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id     TEXT DEFAULT '',
  ts          REAL NOT NULL,
  success     INTEGER NOT NULL,
  ip          TEXT DEFAULT '',
  client_info TEXT DEFAULT '',
  reason      TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_logins_ts ON logins(ts DESC);
"""


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _hash_key(key: str, salt: str) -> str:
    return hashlib.sha256((salt + key).encode("utf-8")).hexdigest()


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _rand_id(prefix: str) -> str:
    return prefix + secrets.token_hex(4)          # 8 位 hex


def _rand_key() -> str:
    return "cbhk_" + secrets.token_urlsafe(32)


def _rand_token() -> str:
    return "cbht_" + secrets.token_urlsafe(32)


def _expiry_iso(ttl_days) -> Optional[str]:
    """ttl_days -> ISO 到期时间；None/'forever'/<=0 表示永久（返回 None）。"""
    if ttl_days is None or ttl_days == "forever" or ttl_days == "null":
        return None
    try:
        days = float(ttl_days)
    except (TypeError, ValueError):
        return None
    if days <= 0:
        return None
    return (datetime.now().astimezone() + timedelta(days=days)).isoformat(
        timespec="seconds")


def _is_expired(expires_at: Optional[str], now: Optional[datetime] = None) -> bool:
    if not expires_at:
        return False
    try:
        dt = datetime.fromisoformat(str(expires_at))
    except (ValueError, TypeError):
        return False
    if dt.tzinfo is None:
        dt = dt.astimezone()
    now = now or datetime.now().astimezone()
    return now > dt


class AdminStore:
    """SQLite 存储（线程安全；WAL 模式）"""

    def __init__(self, db_path: Optional[Path] = None):
        self._lock = threading.RLock()
        self.db_path = Path(db_path) if db_path else (data_dir() / "cbhcli-admin.db")
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        try:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            # 每 ~800KB 自动 checkpoint（默认 1000 页太大，缩短数据
            # 停留在 WAL 中的窗口，降低异常退出场景的数据丢失面）
            self._conn.execute("PRAGMA wal_autocheckpoint=200")
        except Exception:
            pass
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._migrate()
            self._conn.commit()

    def _migrate(self) -> None:
        """旧库结构升级（幂等）：为建表较早的 users 表补 key_plain 列。

        key_plain：用户认证密钥明文（仅管理员可见，供管理页面"查看密钥"）。
        升级前创建的用户该列为空 -> 管理页面提示"仅可查看前缀，如需完整
        密钥请重置"。
        """
        try:
            cols = {r["name"] for r in
                    self._conn.execute("PRAGMA table_info(users)")}
            if "key_plain" not in cols:
                self._conn.execute(
                    "ALTER TABLE users ADD COLUMN key_plain TEXT DEFAULT ''")
        except Exception:
            pass

    # ===============================================================
    #  低层
    # ===============================================================

    def _exec(self, sql: str, params=()):
        with self._lock:
            cur = self._conn.execute(sql, params)
            self._conn.commit()
            return cur

    def _query(self, sql: str, params=()) -> list:
        with self._lock:
            cur = self._conn.execute(sql, params)
            return [dict(r) for r in cur.fetchall()]

    def _query_one(self, sql: str, params=()) -> Optional[dict]:
        rows = self._query(sql, params)
        return rows[0] if rows else None

    # ===============================================================
    #  用户
    # ===============================================================

    @staticmethod
    def _user_public(row: dict) -> dict:
        """对外用户结构（去掉哈希字段）"""
        if not row:
            return {}
        return {
            "id": row.get("id", ""),
            "name": row.get("name", ""),
            "display_name": row.get("display_name", ""),
            "key_prefix": row.get("key_prefix", ""),
            "key_has_plain": bool(row.get("key_plain") or ""),
            "key_version": row.get("key_version", 1),
            "token_ttl_days": row.get("token_ttl_days"),
            "created_at": row.get("created_at", ""),
            "disabled": bool(row.get("disabled", 0)),
            "note": row.get("note", ""),
            "last_login_at": row.get("last_login_at", ""),
            "last_seen_at": row.get("last_seen_at", ""),
        }

    def create_user(self, name: str, display_name: str = "", note: str = "",
                    token_ttl_days=7) -> tuple:
        """创建用户 -> (user_dict, plain_key)。

        plain_key 同时写入 key_plain（管理页面"查看密钥"功能），
        列表 API 只返回前缀；完整密钥经 POST 校验的单独接口获取。
        """
        name = (name or "").strip()
        if not name:
            raise ValueError("用户名不能为空")
        if self.get_user_by_name(name):
            raise ValueError(f"用户名已存在: {name}")
        user_id = _rand_id("u_")
        plain_key = _rand_key()
        salt = secrets.token_hex(16)
        key_prefix = plain_key[:12]
        if token_ttl_days is not None and token_ttl_days != "forever":
            try:
                token_ttl_days = int(token_ttl_days)
            except (TypeError, ValueError):
                token_ttl_days = None
        else:
            token_ttl_days = None
        self._exec(
            "INSERT INTO users (id, name, display_name, key_hash, key_salt,"
            " key_prefix, key_plain, key_version, token_ttl_days, created_at,"
            " disabled, note)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,0,?)",
            (user_id, name, display_name or "", _hash_key(plain_key, salt), salt,
             key_prefix, plain_key, 1, token_ttl_days, now_iso(), note or ""))
        user = self.get_user(user_id) or {}
        return user, plain_key

    def get_user(self, user_id: str) -> Optional[dict]:
        row = self._query_one("SELECT * FROM users WHERE id=?", (user_id,))
        return self._user_public(row) if row else None

    def get_user_raw(self, user_id: str) -> Optional[dict]:
        return self._query_one("SELECT * FROM users WHERE id=?", (user_id,))

    def get_user_by_name(self, name: str) -> Optional[dict]:
        row = self._query_one("SELECT * FROM users WHERE name=?", (name,))
        return self._user_public(row) if row else None

    def list_users(self) -> list:
        rows = self._query("SELECT * FROM users ORDER BY created_at DESC")
        return [self._user_public(r) for r in rows]

    def find_user_by_key(self, key: str) -> Optional[dict]:
        """按认证密钥找用户（登录用）：key_prefix 预筛 + hash 校验。"""
        key = (key or "").strip()
        if not key:
            return None
        prefix = key[:12]
        rows = self._query(
            "SELECT * FROM users WHERE key_prefix=?", (prefix,))
        if not rows:
            # 前缀不可靠（手工导入等）时全量回退（用户数少，可接受）
            rows = self._query("SELECT * FROM users")
        for row in rows:
            if _hash_key(key, row.get("key_salt", "")) == row.get("key_hash"):
                return self._user_public(row)
        return None

    def update_user(self, user_id: str, **fields) -> Optional[dict]:
        allowed = ("display_name", "note", "disabled", "token_ttl_days", "name")
        sets, params = [], []
        for k, v in fields.items():
            if k not in allowed or v is None:
                continue
            if k == "disabled":
                v = 1 if v else 0
            if k == "name":
                v = str(v).strip()
                if not v:
                    continue
                exist = self.get_user_by_name(v)
                if exist and exist.get("id") != user_id:
                    raise ValueError(f"用户名已存在: {v}")
            sets.append(f"{k}=?")
            params.append(v)
        if not sets:
            return self.get_user(user_id)
        params.append(user_id)
        self._exec(f"UPDATE users SET {', '.join(sets)} WHERE id=?", params)
        return self.get_user(user_id)

    def touch_login(self, user_id: str) -> None:
        self._exec("UPDATE users SET last_login_at=? WHERE id=?",
                   (now_iso(), user_id))

    def touch_seen(self, user_id: str) -> None:
        self._exec("UPDATE users SET last_seen_at=? WHERE id=?",
                   (now_iso(), user_id))

    def delete_user(self, user_id: str, purge: bool = False) -> bool:
        """删除用户。purge=True 时级联删除会话/事件/快照。"""
        if not self.get_user(user_id):
            return False
        self._exec("DELETE FROM tokens WHERE user_id=?", (user_id,))
        if purge:
            self._exec("DELETE FROM sessions WHERE user_id=?", (user_id,))
            self._exec("DELETE FROM events WHERE user_id=?", (user_id,))
            try:
                d = snapshot_dir() / user_id
                if d.exists():
                    shutil.rmtree(str(d), ignore_errors=True)
            except Exception:
                pass
        self._exec("DELETE FROM users WHERE id=?", (user_id,))
        return True

    def reset_key(self, user_id: str) -> tuple:
        """重置认证密钥：key_version+1（旧 token 全失效）+ 吊销全部 token。"""
        row = self.get_user_raw(user_id)
        if not row:
            return None, ""
        plain_key = _rand_key()
        salt = secrets.token_hex(16)
        self._exec(
            "UPDATE users SET key_hash=?, key_salt=?, key_prefix=?, key_plain=?,"
            " key_version=key_version+1 WHERE id=?",
            (_hash_key(plain_key, salt), salt, plain_key[:12], plain_key,
             user_id))
        self._exec("UPDATE tokens SET revoked=1 WHERE user_id=?", (user_id,))
        return self.get_user(user_id), plain_key

    def get_user_key(self, user_id: str) -> dict:
        """获取用户密钥明文（管理页面"查看密钥"）。

        Returns: {"key": 明文或"", "prefix": 前缀, "available": bool}
        - available=False：升级前创建的用户（无明文），仅能看前缀
        """
        row = self.get_user_raw(user_id) or {}
        key = str(row.get("key_plain") or "")
        return {
            "key": key,
            "prefix": str(row.get("key_prefix") or ""),
            "available": bool(key),
        }

    def renew_user(self, user_id: str, ttl_days) -> tuple:
        """续期：延长该用户全部未撤销 token 的 expires_at（含已过期的）。

        ttl_days: 天数 | "forever"（永久）
        Returns: (user, tokens_updated, expires_at)
        """
        user = self.get_user(user_id)
        if not user:
            return None, 0, None
        expires_at = _expiry_iso(ttl_days)
        cur = self._exec(
            "UPDATE tokens SET expires_at=? WHERE user_id=? AND revoked=0",
            (expires_at, user_id))
        updated = cur.rowcount if cur is not None else 0
        # 同步更新用户默认有效期策略（后续新登录沿用）
        try:
            self.update_user(user_id, token_ttl_days=(
                None if expires_at is None else
                (int(ttl_days) if str(ttl_days).isdigit() else user.get("token_ttl_days"))))
        except Exception:
            pass
        return self.get_user(user_id), updated, expires_at

    # ===============================================================
    #  令牌
    # ===============================================================

    def issue_token(self, user_id: str, ttl_days, client_info: str = "") -> tuple:
        """签发 token -> (plain_token, expires_at)"""
        raw = self.get_user_raw(user_id) or {}
        expires_at = _expiry_iso(ttl_days)
        token = _rand_token()
        self._exec(
            "INSERT INTO tokens (id, user_id, token_hash, created_at, expires_at,"
            " revoked, key_version, client_info) VALUES (?,?,?,?,?,0,?,?)",
            (_rand_id("t_"), user_id, _hash_token(token), now_iso(), expires_at,
             int(raw.get("key_version", 1) or 1), client_info or ""))
        return token, expires_at

    def verify_token(self, token: str) -> Optional[dict]:
        """校验 token（收口）。

        通过条件：token_hash 匹配 AND revoked=0 AND 未过期 AND 用户未禁用
        AND token 签发时的 key_version == 用户当前 key_version。
        Returns: {"user": {...}, "token": {...}} 或 None
        """
        token = (token or "").strip()
        if not token:
            return None
        row = self._query_one("SELECT * FROM tokens WHERE token_hash=?",
                              (_hash_token(token),))
        if not row or row.get("revoked"):
            return None
        if _is_expired(row.get("expires_at")):
            return None
        user_raw = self.get_user_raw(row.get("user_id", "") or "")
        if not user_raw or user_raw.get("disabled"):
            return None
        if int(row.get("key_version", 1) or 1) != int(
                user_raw.get("key_version", 1) or 1):
            return None
        return {
            "user": self._user_public(user_raw),
            "token": {
                "id": row.get("id", ""),
                "expires_at": row.get("expires_at"),
                "created_at": row.get("created_at", ""),
                "client_info": row.get("client_info", ""),
            },
        }

    def revoke_token(self, token: str) -> bool:
        cur = self._exec("UPDATE tokens SET revoked=1 WHERE token_hash=?",
                         (_hash_token(token or ""),))
        return bool(cur and cur.rowcount)

    def revoke_user_tokens(self, user_id: str) -> int:
        cur = self._exec("UPDATE tokens SET revoked=1 WHERE user_id=?", (user_id,))
        return cur.rowcount if cur else 0

    def list_user_tokens(self, user_id: str, active_only: bool = False) -> list:
        sql = "SELECT * FROM tokens WHERE user_id=?"
        if active_only:
            sql += " AND revoked=0"
        rows = self._query(sql, (user_id,))
        out = []
        for r in rows:
            out.append({
                "id": r.get("id", ""),
                "created_at": r.get("created_at", ""),
                "expires_at": r.get("expires_at"),
                "revoked": bool(r.get("revoked", 0)),
                "client_info": r.get("client_info", ""),
            })
        return out

    # ===============================================================
    #  会话
    # ===============================================================

    def upsert_session(self, user_id: str, session_id: str, **fields) -> None:
        existing = self._query_one(
            "SELECT * FROM sessions WHERE user_id=? AND session_id=?",
            (user_id, session_id))
        if existing:
            sets, params = [], []
            for k in ("agent", "workspace", "title", "model", "status",
                      "created_at", "updated_at", "last_heartbeat",
                      "message_count"):
                if k in fields and fields[k] is not None:
                    sets.append(f"{k}=?")
                    params.append(fields[k])
            if not sets:
                return
            params += [user_id, session_id]
            self._exec(
                f"UPDATE sessions SET {', '.join(sets)}"
                " WHERE user_id=? AND session_id=?", params)
        else:
            self._exec(
                "INSERT INTO sessions (user_id, session_id, agent, workspace,"
                " title, model, status, created_at, updated_at, last_heartbeat,"
                " message_count) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (user_id, session_id,
                 fields.get("agent", ""), fields.get("workspace", ""),
                 fields.get("title", ""), fields.get("model", ""),
                 fields.get("status", "idle"), fields.get("created_at", ""),
                 fields.get("updated_at", ""),
                 float(fields.get("last_heartbeat", 0) or 0),
                 int(fields.get("message_count", 0) or 0)))

    def list_sessions(self, user_id: str = "", status: str = "", q: str = "",
                      from_ts: float = 0, to_ts: float = 0,
                      limit: int = 100, offset: int = 0) -> tuple:
        """会话列表（筛选/分页）-> (items, total)"""
        where, params = [], []
        if user_id:
            where.append("s.user_id=?")
            params.append(user_id)
        if status:
            where.append("s.status=?")
            params.append(status)
        if q:
            where.append("(s.title LIKE ? OR s.session_id LIKE ?"
                         " OR s.agent LIKE ? OR s.workspace LIKE ?)")
            like = f"%{q}%"
            params += [like, like, like, like]
        if from_ts:
            where.append("s.last_heartbeat>=?")
            params.append(float(from_ts))
        if to_ts:
            where.append("s.last_heartbeat<=?")
            params.append(float(to_ts))
        wsql = (" WHERE " + " AND ".join(where)) if where else ""
        total = self._query_one(
            f"SELECT COUNT(*) AS c FROM sessions s{wsql}", params)
        rows = self._query(
            f"SELECT s.*, u.name AS user_name, u.display_name AS user_display"
            f" FROM sessions s LEFT JOIN users u ON u.id = s.user_id"
            f"{wsql} ORDER BY s.updated_at DESC, s.last_heartbeat DESC"
            f" LIMIT ? OFFSET ?", params + [int(limit), int(offset)])
        return rows, int((total or {}).get("c", 0) or 0)

    def get_session(self, user_id: str, session_id: str) -> Optional[dict]:
        return self._query_one(
            "SELECT * FROM sessions WHERE user_id=? AND session_id=?",
            (user_id, session_id))

    def delete_session(self, user_id: str, session_id: str) -> bool:
        self._exec("DELETE FROM sessions WHERE user_id=? AND session_id=?",
                   (user_id, session_id))
        self.delete_snapshot(user_id, session_id)
        return True

    # ===============================================================
    #  事件
    # ===============================================================

    def insert_events(self, user_id: str, events: list,
                      accepted_out: Optional[list] = None) -> int:
        """批量写入事件（eid 幂等去重）-> 实际接受数。

        accepted_out：可选列表；传入时把实际入库（非重复）的事件追加进去
        （report_api 转发实时画面用——重复回放的事件不重复推送）。
        """
        accepted = 0
        recv = time.time()
        with self._lock:
            for ev in events or []:
                if not isinstance(ev, dict):
                    continue
                try:
                    payload = json.dumps(ev.get("payload") or {},
                                         ensure_ascii=False)
                except Exception:
                    payload = "{}"
                try:
                    cur = self._conn.execute(
                        "INSERT OR IGNORE INTO events (eid, user_id, session_id,"
                        " ts, recv_ts, type, payload) VALUES (?,?,?,?,?,?,?)",
                        (ev.get("eid") or "", user_id,
                         ev.get("session_id", "") or "",
                         float(ev.get("ts", 0) or 0), recv,
                         str(ev.get("type", "unknown"))[:80], payload))
                    if cur.rowcount:
                        accepted += 1
                        if accepted_out is not None:
                            accepted_out.append(ev)
                except Exception:
                    continue
            self._conn.commit()
        return accepted

    def list_events(self, type_: str = "", user_id: str = "",
                    session_id: str = "", from_ts: float = 0, to_ts: float = 0,
                    limit: int = 100, offset: int = 0,
                    order_desc: bool = True) -> tuple:
        where, params = [], []
        if type_:
            where.append("e.type=?")
            params.append(type_)
        if user_id:
            where.append("e.user_id=?")
            params.append(user_id)
        if session_id:
            where.append("e.session_id=?")
            params.append(session_id)
        if from_ts:
            where.append("e.recv_ts>=?")
            params.append(float(from_ts))
        if to_ts:
            where.append("e.recv_ts<=?")
            params.append(float(to_ts))
        wsql = (" WHERE " + " AND ".join(where)) if where else ""
        total = self._query_one(
            f"SELECT COUNT(*) AS c FROM events e{wsql}", params)
        order = "DESC" if order_desc else "ASC"
        rows = self._query(
            f"SELECT e.*, u.name AS user_name, u.display_name AS user_display"
            f" FROM events e LEFT JOIN users u ON u.id = e.user_id"
            f"{wsql} ORDER BY e.recv_ts {order}, e.id {order}"
            f" LIMIT ? OFFSET ?", params + [int(limit), int(offset)])
        for r in rows:
            try:
                r["payload_obj"] = json.loads(r.get("payload") or "{}")
            except Exception:
                r["payload_obj"] = {}
        return rows, int((total or {}).get("c", 0) or 0)

    def events_for_export(self, **kw) -> list:
        """导出用：无分页（上限 50000 条）。"""
        kw["limit"] = 50000
        kw["offset"] = 0
        kw["order_desc"] = False
        rows, _ = self.list_events(**kw)
        return rows

    def recent_events(self, limit: int = 30) -> list:
        rows, _ = self.list_events(limit=limit)
        return rows

    # ===============================================================
    #  登录记录
    # ===============================================================

    def add_login(self, user_id: str, success: bool, ip: str = "",
                  client_info: str = "", reason: str = "") -> None:
        try:
            self._exec(
                "INSERT INTO logins (user_id, ts, success, ip, client_info,"
                " reason) VALUES (?,?,?,?,?,?)",
                (user_id or "", time.time(), 1 if success else 0, ip or "",
                 client_info or "", reason or ""))
        except Exception:
            pass

    def list_logins(self, user_id: str = "", success: Optional[bool] = None,
                    limit: int = 100, offset: int = 0) -> tuple:
        where, params = [], []
        if user_id:
            where.append("l.user_id=?")
            params.append(user_id)
        if success is not None:
            where.append("l.success=?")
            params.append(1 if success else 0)
        wsql = (" WHERE " + " AND ".join(where)) if where else ""
        total = self._query_one(
            f"SELECT COUNT(*) AS c FROM logins l{wsql}", params)
        rows = self._query(
            f"SELECT l.*, u.name AS user_name, u.display_name AS user_display"
            f" FROM logins l LEFT JOIN users u ON u.id = l.user_id"
            f"{wsql} ORDER BY l.ts DESC LIMIT ? OFFSET ?",
            params + [int(limit), int(offset)])
        return rows, int((total or {}).get("c", 0) or 0)

    # ===============================================================
    #  快照文件
    # ===============================================================

    def save_snapshot(self, user_id: str, session_id: str, data: dict) -> bool:
        """原子写入快照文件。"""
        try:
            d = snapshot_dir() / (user_id or "unknown")
            d.mkdir(parents=True, exist_ok=True)
            p = d / f"{session_id}.json"
            tmp = d / f".{session_id}.json.tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(str(tmp), str(p))
            return True
        except Exception:
            return False

    def load_snapshot(self, user_id: str, session_id: str) -> Optional[dict]:
        try:
            p = snapshot_dir() / (user_id or "unknown") / f"{session_id}.json"
            if not p.exists():
                return None
            with open(p, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return None

    def delete_snapshot(self, user_id: str, session_id: str) -> None:
        try:
            p = snapshot_dir() / (user_id or "unknown") / f"{session_id}.json"
            if p.exists():
                p.unlink()
        except Exception:
            pass

    # ===============================================================
    #  统计 / 健康
    # ===============================================================

    def overview(self) -> dict:
        users = self._query_one("SELECT COUNT(*) AS c FROM users") or {}
        today0 = datetime.now().astimezone().replace(
            hour=0, minute=0, second=0, microsecond=0).timestamp()
        today_sessions = self._query_one(
            "SELECT COUNT(*) AS c FROM sessions WHERE last_heartbeat>=?",
            (today0,)) or {}
        today_events = self._query_one(
            "SELECT COUNT(*) AS c FROM events WHERE recv_ts>=?", (today0,)) or {}
        total_events = self._query_one(
            "SELECT COUNT(*) AS c FROM events") or {}
        total_sessions = self._query_one(
            "SELECT COUNT(*) AS c FROM sessions") or {}
        last_event = self._query_one(
            "SELECT recv_ts FROM events ORDER BY recv_ts DESC LIMIT 1") or {}
        return {
            "users": int(users.get("c", 0) or 0),
            "today_sessions": int(today_sessions.get("c", 0) or 0),
            "today_events": int(today_events.get("c", 0) or 0),
            "total_events": int(total_events.get("c", 0) or 0),
            "total_sessions": int(total_sessions.get("c", 0) or 0),
            "last_event_ts": float(last_event.get("recv_ts", 0) or 0),
        }

    def db_size_bytes(self) -> int:
        try:
            return self.db_path.stat().st_size
        except Exception:
            return 0

    def snapshot_count(self) -> int:
        try:
            root = snapshot_dir()
            return sum(1 for _ in root.rglob("*.json"))
        except Exception:
            return 0

    def maybe_backup(self) -> Optional[str]:
        """数据库每日备份 1 份（保留 30 份）。"""
        try:
            backup_dir = data_dir() / "backup"
            backup_dir.mkdir(parents=True, exist_ok=True)
            today = datetime.now().strftime("%Y%m%d")
            target = backup_dir / f"cbhcli-admin_{today}.db"
            if target.exists():
                return None
            shutil.copy2(str(self.db_path), str(target))
            files = sorted(backup_dir.glob("cbhcli-admin_*.db"))
            while len(files) > 30:
                oldest = files.pop(0)
                try:
                    oldest.unlink()
                except Exception:
                    break
            return str(target)
        except Exception:
            return None


def get_store() -> AdminStore:
    global _store_singleton
    if _store_singleton is None:
        with _singleton_lock:
            if _store_singleton is None:
                _store_singleton = AdminStore()
    return _store_singleton
