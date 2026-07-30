"""全局配置管理"""
import json
from pathlib import Path
from typing import Optional
from datetime import datetime


# 配置目录 - 统一在 ~/.cbhcli
CBHCLI_DIR = Path.home() / '.cbhcli'
CONFIG_FILE = CBHCLI_DIR / 'config.json'


class GlobalConfig:
    """全局配置管理"""
    
    def __init__(self):
        CBHCLI_DIR.mkdir(parents=True, exist_ok=True)
        self.config = self._load_config()
    
    def _load_config(self) -> dict:
        """加载配置"""
        if CONFIG_FILE.exists():
            try:
                with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception:
                pass
        
        return self._default_config()
    
    def _default_config(self) -> dict:
        """默认配置"""
        return {
            "models": [],
            "embedding_model": None,  # 嵌入模型配置
            "rerank_model": None,     # 重排序模型配置
            "fallback_models": [],          # 主模型备用顺序（模型名称列表）
            "fallback_vision_models": [],   # 视觉模型备用顺序（模型名称列表）
            "agents": {
                "default_agent": "main",
                "active_agent": None
            },
            "settings": {
                "auto_compress": True,
                "compression_ratio": 0.8,
                "workspace_base": str(CBHCLI_DIR / "agents"),
                "use_chromadb_embedding": True,  # 是否使用 ChromaDB 内置嵌入模型
                "knowledge_base_dir": str(CBHCLI_DIR / "agents"),  # 知识库根目录
            }
        }
    
    def save(self) -> None:
        """保存配置"""
        CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(self.config, f, indent=2, ensure_ascii=False)
    
    # 模型管理
    def get_models(self) -> list[dict]:
        """获取所有模型"""
        return self.config.get("models", [])
    
    def add_model(self, model: dict) -> None:
        """添加模型"""
        self.config.setdefault("models", []).append(model)
        self.save()
    
    def delete_model(self, model_name: str) -> bool:
        """删除模型"""
        models = self.config.get("models", [])
        for i, m in enumerate(models):
            if m.get("name") == model_name:
                models.pop(i)
                self.save()
                return True
        return False
    
    def get_model(self, model_name: str) -> Optional[dict]:
        """获取模型配置"""
        for model in self.config.get("models", []):
            if model.get("name") == model_name:
                return model
        return None
    
    def get_last_selected_model(self) -> Optional[str]:
        """获取上次选择的模型"""
        return self.config.get("last_selected_model")
    
    def set_last_selected_model(self, model_name: str) -> None:
        """保存上次选择的模型"""
        self.config["last_selected_model"] = model_name
        self.save()
    
    # Agent管理
    def get_active_agent(self) -> Optional[str]:
        """获取当前Agent"""
        return self.config.get("agents", {}).get("active_agent")
    
    def set_active_agent(self, agent_name: str) -> None:
        """设置当前Agent"""
        self.config.setdefault("agents", {})["active_agent"] = agent_name
        self.save()
    
    def get_default_agent(self) -> str:
        """获取默认Agent名称"""
        return self.config.get("agents", {}).get("default_agent", "general")

    # Agent 链条激活状态持久化（per-agent）
    def get_active_chain(self, agent_name: str) -> Optional[str]:
        """获取指定 Agent 当前激活的链条名称"""
        return self.config.get("agents", {}).get("active_chains", {}).get(agent_name)

    def set_active_chain(self, agent_name: str, chain_name: Optional[str]) -> None:
        """设置指定 Agent 激活的链条（chain_name=None 表示取消）"""
        chains = self.config.setdefault("agents", {}).setdefault("active_chains", {})
        if chain_name:
            chains[agent_name] = chain_name
        else:
            chains.pop(agent_name, None)
        self.save()
    
    # 设置
    def get_settings(self) -> dict:
        """获取设置"""
        return self.config.get("settings", {})
    
    def update_setting(self, key: str, value) -> None:
        """更新设置"""
        self.config.setdefault("settings", {})[key] = value
        self.save()
    
    # 嵌入模型管理
    def get_embedding_model(self) -> Optional[dict]:
        """获取嵌入模型配置"""
        return self.config.get("embedding_model")
    
    def set_embedding_model(self, model_config: dict) -> None:
        """设置嵌入模型配置
        
        Args:
            model_config: {name, apiKey, url, model, type}
                         type: "openai" | "custom"
        """
        self.config["embedding_model"] = model_config
        self.save()
    
    def delete_embedding_model(self) -> None:
        """删除嵌入模型配置"""
        self.config["embedding_model"] = None
        self.save()
    
    # 重排序模型管理
    def get_rerank_model(self) -> Optional[dict]:
        """获取重排序模型配置"""
        return self.config.get("rerank_model")
    
    def set_rerank_model(self, model_config: dict) -> None:
        """设置重排序模型配置
        
        Args:
            model_config: {name, apiKey, url, model, top_n}
        """
        self.config["rerank_model"] = model_config
        self.save()
    
    def delete_rerank_model(self) -> None:
        """删除重排序模型配置"""
        self.config["rerank_model"] = None
        self.save()
    
    # 备用模型管理
    def get_fallback_models(self) -> list[str]:
        """获取主模型备用顺序（模型名称列表）"""
        return self.config.get("fallback_models", [])
    
    def set_fallback_models(self, model_names: list[str]) -> None:
        """设置主模型备用顺序"""
        self.config["fallback_models"] = model_names
        self.save()
    
    def get_fallback_vision_models(self) -> list[str]:
        """获取视觉模型备用顺序（模型名称列表）"""
        return self.config.get("fallback_vision_models", [])
    
    def set_fallback_vision_models(self, model_names: list[str]) -> None:
        """设置视觉模型备用顺序"""
        self.config["fallback_vision_models"] = model_names
        self.save()
