"""管理员主密码校验（启动门）+ admin.json 配置读写

校验源优先级：
  ① 环境变量 CBHCLI_ADMIN_MASTER_KEY（如设置，最高优先）
  ② 数据目录 admin.json 中的 master_key_hash（首次通过 ①/③ 启动后自动写入）
  ③ 代码内置默认口令（**仅存 hash，源码不含明文**；口令不公开，
     需要时请联系 cbhcli 发行方，或使用 --set-password 自行更换）

全部用 hmac.compare_digest 防时序攻击；hash = sha256(salt + key)。
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
from typing import Optional

from cbhcli_pkg.admin import data_dir

# 内置默认口令：hash 存储（源码/whl 中不含明文，防"解包即见密码"）
# 口令本身为随机强口令（非弱口令），离线穷举不可行；实际部署建议设置
# 环境变量或使用 --set-password 更换为自己的口令。
DEFAULT_MASTER_KEY_SALT = "3cf0b493f41ef6064d6d77e811e02cd9"
DEFAULT_MASTER_KEY_HASH = ("822f86b2aed595853bad3527c0e6daccc975ca04df6e484924f646a2f"
                           "630a0c6")
_ENV_KEY = "CBHCLI_ADMIN_MASTER_KEY"

# 服务端设置默认值（admin.json 的 settings 段）
DEFAULT_SETTINGS = {
    "strict_online": False,          # 严格在线模式（客户端离线超宽限即拦截）
    "strict_grace_minutes": 30,
    "report_level": "full",          # full | summary
    "default_token_ttl_days": 30,    # 新用户默认 token 有效期（null=永久）
    "snapshot_max_mb": 10,
}


def admin_json_path():
    return data_dir() / "admin.json"


def load_admin_json() -> dict:
    try:
        p = admin_json_path()
        if p.exists():
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
    except Exception:
        pass
    return {}


def save_admin_json(data: dict) -> bool:
    try:
        p = admin_json_path()
        tmp = p.with_suffix(".json.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.chmod(str(tmp), 0o600)
        os.replace(str(tmp), str(p))
        try:
            os.chmod(str(p), 0o600)
        except Exception:
            pass
        return True
    except Exception:
        return False


# ===================================================================
#  主密码
# ===================================================================

def _hash_key(key: str, salt: str) -> str:
    return hashlib.sha256((salt + key).encode("utf-8")).hexdigest()


def _env_master_key() -> str:
    return os.environ.get(_ENV_KEY, "").strip()


def verify_master_key(input_key: str) -> bool:
    """校验管理员主密码（优先级 env > admin.json > 内置默认）。"""
    if not input_key:
        return False

    env_key = _env_master_key()
    if env_key:
        return hmac.compare_digest(input_key, env_key)

    data = load_admin_json()
    stored_hash = data.get("master_key_hash", "")
    salt = data.get("master_key_salt", "")
    if stored_hash and salt:
        return hmac.compare_digest(_hash_key(input_key, salt), stored_hash)

    # 内置默认（hash 比较；源码不含明文口令）
    return hmac.compare_digest(
        _hash_key(input_key, DEFAULT_MASTER_KEY_SALT), DEFAULT_MASTER_KEY_HASH)


def store_master_key(key: str) -> bool:
    """校验通过后写入 admin.json（sha256+salt）。"""
    if not key:
        return False
    salt = secrets.token_hex(16)
    data = load_admin_json()
    data["master_key_hash"] = _hash_key(key, salt)
    data["master_key_salt"] = salt
    return save_admin_json(data)


def master_key_source() -> str:
    """当前校验源说明（启动提示用）。"""
    if _env_master_key():
        return "环境变量 CBHCLI_ADMIN_MASTER_KEY"
    data = load_admin_json()
    if data.get("master_key_hash"):
        return "admin.json 已存储的主密码"
    return "内置默认主密码"


def set_new_master_key(new_key: str) -> bool:
    """修改主密码（--set-password 用）。"""
    return store_master_key(new_key)


# ===================================================================
#  服务端设置
# ===================================================================

def get_settings() -> dict:
    data = load_admin_json()
    settings = dict(DEFAULT_SETTINGS)
    stored = data.get("settings")
    if isinstance(stored, dict):
        settings.update(stored)
    return settings


def update_settings(patch: dict) -> dict:
    data = load_admin_json()
    settings = dict(DEFAULT_SETTINGS)
    stored = data.get("settings")
    if isinstance(stored, dict):
        settings.update(stored)
    for k, v in (patch or {}).items():
        if k in DEFAULT_SETTINGS or k in ("admin_note",):
            settings[k] = v
    data["settings"] = settings
    save_admin_json(data)
    return settings
