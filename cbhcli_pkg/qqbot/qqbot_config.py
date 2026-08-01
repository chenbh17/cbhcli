"""QQ Bot 配置管理

存储和读取 QQ Bot 的 AppID、AppSecret 等配置信息。
配置持久化到 ~/.cbhcli/config.json 中的 qqbots 字段。
"""
import json
import os
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional


@dataclass
class QQBotConfig:
    """单个 QQ Bot 的配置"""

    name: str                          # Bot 名称
    appId: str                         # QQ 开放平台 AppID
    appSecret: str = ""                # QQ 开放平台 AppSecret
    clientSecret: str = ""             # ClientSecret（部分沙箱环境需要）
    intents: int = 33555456            # 事件监听位掩码
    sandbox: bool = True               # 是否沙箱模式
    enabled: bool = True               # 是否启用
    target_agent: str = ""             # 目标 Agent
    description: str = ""              # Bot 描述

    @property
    def token(self) -> str:
        """获取网关鉴权 Token

        格式: QQBot {secret}
        secret 优先使用 clientSecret，次选 appSecret。
        """
        secret = self.clientSecret or self.appSecret
        return f"QQBot {secret}"

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "QQBotConfig":
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


class QQBotConfigManager:
    """QQ Bot 配置管理器

    配置持久化到 ~/.cbhcli/config.json 的 qqbots 字段。
    """

    CONFIG_PATH = Path.home() / ".cbhcli" / "config.json"

    def __init__(self, config_path: Optional[Path] = None):
        self.config_path = config_path or self.CONFIG_PATH
        self._bots: dict[str, QQBotConfig] = {}
        self._load()

    def _load(self):
        """从配置文件加载"""
        if not self.config_path.exists():
            return
        try:
            with open(self.config_path, 'r') as f:
                data = json.load(f)
            raw_bots = data.get('qqbots', [])
            for item in raw_bots:
                bot = QQBotConfig.from_dict(item)
                self._bots[bot.name] = bot
        except (json.JSONDecodeError, KeyError):
            pass

    def _save(self):
        """保存到配置文件"""
        # 读取现有配置
        if self.config_path.exists():
            with open(self.config_path, 'r') as f:
                data = json.load(f)
        else:
            data = {}

        # 更新 qqbots 字段
        data['qqbots'] = [bot.to_dict() for bot in self._bots.values()]

        # 写回
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.config_path, 'w') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    def add(self, config: QQBotConfig):
        """添加或更新 Bot 配置"""
        self._bots[config.name] = config
        self._save()

    def remove(self, name: str) -> bool:
        """删除 Bot 配置"""
        if name in self._bots:
            del self._bots[name]
            self._save()
            return True
        return False

    def get(self, name: str) -> Optional[QQBotConfig]:
        """获取 Bot 配置"""
        return self._bots.get(name)

    def list_all(self) -> list[QQBotConfig]:
        """列出所有 Bot 配置"""
        return list(self._bots.values())

    def set_enabled(self, name: str, enabled: bool):
        """设置 Bot 启用状态"""
        if name in self._bots:
            self._bots[name].enabled = enabled
            self._save()
