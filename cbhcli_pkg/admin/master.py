"""管理员主密码校验（启动门）+ admin.json 配置读写

校验源优先级（v5.4.1 修复）：
  ① admin.json 中 **显式修改过**（key_source="explicit"）的主密码 hash
     —— 经由管理页面「修改主密码」或 `cbhcli admin --set-password` 写入，
     **最高优先**：否则启动配置里的环境变量会让"改密码"永远不生效
     （老密码继续可用、新密码登录失败——用户实际报告的 bug）
  ② 环境变量 CBHCLI_ADMIN_MASTER_KEY（未显式修改过时的部署凭据）
  ③ admin.json 中引导写入（key_source="bootstrap"）的 master_key_hash
     （首次通过 ①/② 或内置默认启动后自动写入）
  ④ 代码内置默认口令（**仅存 hash，源码不含明文**；口令不公开，
     需要时请联系 cbhcli 发行方，或使用 --set-password 自行更换）

安全设计（用户要求，2026-09-16）：**改密码/启动均需校验当前主密码**——
`--set-password` 必须验证当前密码（不存在跳过校验的通道）；
启动门（server._pass_gate）对"环境变量与当前主密码不一致"提示但继续启动
（以已修改的主密码为准，改密码后无需同步启动配置）。
如确需恢复"由环境变量主导"（运维级操作，需 admin.json 文件写权限）：
删除该文件中 master_key_hash / master_key_salt / key_source 三个字段后重启。

全部用 hmac.compare_digest 防时序攻击；hash = sha256(salt + key)。
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import time
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
    "report_level": "full",          # full | summary（summary=跳过实时流式内容）
    "default_token_ttl_days": 30,    # 新用户默认 token 有效期（null=永久）
    "snapshot_max_mb": 10,
    "auto_renew": True,              # 剩余 <2 天时自动滚动续期（false=需人工续期）
}

# 设置项校验规则（类型 / 范围；管理 API 写入前校验，非法值返回 400）
SETTINGS_SPEC = {
    "strict_online": {"type": "bool"},
    "strict_grace_minutes": {"type": "int", "min": 0, "max": 10080},
    "report_level": {"type": "enum", "values": ("full", "summary")},
    "default_token_ttl_days": {"type": "int_or_null", "min": 1, "max": 3650},
    "snapshot_max_mb": {"type": "int", "min": 1, "max": 1024},
    "auto_renew": {"type": "bool"},
}
_ADMIN_NOTE_MAX = 200


def _check_setting(key: str, value) -> tuple:
    """校验单个设置项。Returns: (ok: bool, err: str)。"""
    spec = SETTINGS_SPEC.get(key)
    if spec is None:
        return False, f"未知设置项: {key}"
    t = spec["type"]
    if t == "bool":
        if not isinstance(value, bool):
            return False, f"{key} 必须为布尔值（true/false）"
        return True, ""
    if t == "enum":
        if value not in spec["values"]:
            return False, f"{key} 必须是 {' / '.join(spec['values'])} 之一"
        return True, ""
    if t == "int_or_null" and value is None:
        return True, ""
    if t == "int" or t == "int_or_null":
        if isinstance(value, bool) or not isinstance(value, int):
            return False, f"{key} 必须为整数"
        if not (spec["min"] <= value <= spec["max"]):
            return False, (f"{key} 必须在 {spec['min']}~{spec['max']} 之间"
                           f"（当前 {value}）")
        return True, ""
    return False, f"{key} 校验规则缺失"


def validate_settings_patch(patch) -> tuple:
    """校验设置补丁（管理 API 用）。Returns: (ok: bool, err: str)。"""
    if not isinstance(patch, dict):
        return False, "请求体必须是 JSON 对象"
    for k, v in patch.items():
        if k == "admin_note":
            if v is None or v == "":
                continue
            if not isinstance(v, str) or len(v) > _ADMIN_NOTE_MAX:
                return False, f"admin_note 必须是不超过 {_ADMIN_NOTE_MAX} 字符的文本"
            continue
        ok, err = _check_setting(k, v)
        if not ok:
            return False, err
    return True, ""


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


def _stored_key(data: dict) -> tuple:
    """admin.json 中存储的 (hash, salt)。"""
    h = str(data.get("master_key_hash", "") or "")
    s = str(data.get("master_key_salt", "") or "")
    if h and s:
        return h, s
    return "", ""


def is_explicit_key() -> bool:
    """admin.json 中是否存有"显式修改过"的主密码（优先于环境变量）。

    显式修改的来源：管理页面「修改主密码」/ `cbhcli admin --set-password`。
    """
    data = load_admin_json()
    h, _s = _stored_key(data)
    return bool(h and data.get("key_source") == "explicit")


def verify_master_key(input_key: str) -> bool:
    """校验管理员主密码（优先级：显式修改 > 环境变量 > 引导写入 > 内置默认）。"""
    if not input_key:
        return False

    data = load_admin_json()
    stored_hash, salt = _stored_key(data)

    # ① 显式修改过的主密码 = 唯一权威（环境变量不再生效，确保"改密码"立即生效）
    if stored_hash and data.get("key_source") == "explicit":
        return hmac.compare_digest(_hash_key(input_key, salt), stored_hash)

    # ② 环境变量（部署凭据；仅在未显式修改过时生效）
    env_key = _env_master_key()
    if env_key:
        return hmac.compare_digest(input_key, env_key)

    # ③ 引导写入的 hash
    if stored_hash:
        return hmac.compare_digest(_hash_key(input_key, salt), stored_hash)

    # ④ 内置默认（hash 比较；源码不含明文口令）
    return hmac.compare_digest(
        _hash_key(input_key, DEFAULT_MASTER_KEY_SALT), DEFAULT_MASTER_KEY_HASH)


def store_master_key(key: str, explicit: bool = False) -> bool:
    """写入主密码 hash（sha256+salt）。

    explicit=True : 显式修改（管理页面 / --set-password）——标记
                    key_source="explicit"，此后校验优先于环境变量。
    explicit=False: 引导/启动时写入——保留已有的 "explicit" 标记（不降级）。
    """
    if not key:
        return False
    salt = secrets.token_hex(16)
    data = load_admin_json()
    if not explicit and data.get("key_source") != "explicit":
        data["key_source"] = "bootstrap"
    elif explicit:
        data["key_source"] = "explicit"
    data["master_key_hash"] = _hash_key(key, salt)
    data["master_key_salt"] = salt
    data["key_updated_at"] = time.time()
    return save_admin_json(data)


def master_key_source() -> str:
    """当前校验源说明（启动提示 / 管理页面设置页用）。"""
    data = load_admin_json()
    stored_hash, _salt = _stored_key(data)
    if stored_hash and data.get("key_source") == "explicit":
        if _env_master_key():
            return "admin.json（已修改的主密码；环境变量已不再生效）"
        return "admin.json（已修改的主密码）"
    if _env_master_key():
        return "环境变量 CBHCLI_ADMIN_MASTER_KEY"
    if stored_hash:
        return "admin.json 已存储的主密码"
    return "内置默认主密码"


def set_new_master_key(new_key: str) -> bool:
    """修改主密码（管理页面 / --set-password 用；显式标记，立即生效）。"""
    return store_master_key(new_key, explicit=True)


def sync_process_env(new_key: str) -> None:
    """改密码后同步**进程内**环境变量为新密码。

    说明：外部启动配置（crontab/systemd）中的 CBHCLI_ADMIN_MASTER_KEY 由
    启动方定义，运行中的进程无法修改它——**也不需要修改**：改密码后
    以"已修改的主密码"为准（优先级最高），环境变量的旧值不再作为凭据。
    这里同步进程内副本只是保持内部一致（日志/审计展示等）。
    """
    if not new_key:
        return
    if os.environ.get(_ENV_KEY, "").strip():
        os.environ[_ENV_KEY] = new_key


# ===================================================================
#  服务端设置
# ===================================================================

def get_settings() -> dict:
    """读取服务端设置（缺失/非法值回落到默认值）。"""
    data = load_admin_json()
    settings = dict(DEFAULT_SETTINGS)
    stored = data.get("settings")
    if isinstance(stored, dict):
        for k, v in stored.items():
            if k in DEFAULT_SETTINGS:
                ok, _err = _check_setting(k, v)
                settings[k] = v if ok else DEFAULT_SETTINGS[k]
            elif k == "admin_note" and isinstance(v, str):
                settings[k] = v
    return settings


def update_settings(patch: dict) -> dict:
    """更新服务端设置（非法值忽略；API 层已先行校验并返回 400）。"""
    patch = patch if isinstance(patch, dict) else {}
    clean = {}
    for k, v in patch.items():
        if k == "admin_note":
            if isinstance(v, str) and len(v) <= _ADMIN_NOTE_MAX:
                clean[k] = v
            continue
        ok, _err = _check_setting(k, v)
        if ok:
            clean[k] = v
    data = load_admin_json()
    settings = get_settings()
    settings.update(clean)
    data["settings"] = settings
    save_admin_json(data)
    return settings
