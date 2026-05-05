"""嵌入模型客户端 - 支持多种嵌入模型 API"""
import requests
from typing import Optional


class EmbeddingClient:
    """嵌入模型客户端
    
    支持:
    - OpenAI 兼容 API (text-embedding-3-small, text-embedding-ada-002)
    - 通义千问 embedding
    - 其他 OpenAI 兼容的嵌入模型
    """
    
    def __init__(self, config: dict):
        """
        初始化嵌入模型客户端
        
        Args:
            config: 配置字典 {name, apiKey, url, model, type}
        """
        self.name = config.get("name", "embedding")
        self.base_url = config["url"].rstrip('/')
        self.api_key = config["apiKey"]
        self.model = config.get("model", "text-embedding-3-small")
        self.model_type = config.get("type", "openai")  # openai | custom
        
        self._session = requests.Session()
        self._session.headers.update({
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        })
    
    def embed(self, texts: list[str]) -> list[list[float]]:
        """
        获取文本的 embedding 向量
        
        Args:
            texts: 文本列表
            
        Returns:
            embedding 向量列表
        """
        if self.model_type == "openai":
            return self._openai_embed_with_batch(texts)
        else:
            return self._custom_embed_with_batch(texts)
    
    def _openai_embed_with_batch(self, texts: list[str], batch_size: int = 10) -> list[list[float]]:
        """OpenAI 兼容 API 格式，支持分批处理
        
        Args:
            texts: 文本列表
            batch_size: 每批处理的文本数量（默认 10，符合大多数 API 限制）
            
        Returns:
            embedding 向量列表
        """
        if not texts:
            return []
        
        all_embeddings = []
        
        # 分批处理
        for i in range(0, len(texts), batch_size):
            batch = texts[i:i + batch_size]
            batch_embeddings = self._openai_embed(batch)
            all_embeddings.extend(batch_embeddings)
        
        return all_embeddings
    
    def _openai_embed(self, texts: list[str]) -> list[list[float]]:
        """OpenAI 兼容 API 格式（单次请求）"""
        payload = {
            "model": self.model,
            "input": texts,
            "encoding_format": "float"
        }
        
        response = self._session.post(
            f"{self.base_url}/embeddings",
            json=payload,
            timeout=30
        )
        
        if response.status_code != 200:
            raise Exception(f"Embedding 请求失败: {response.status_code} - {response.text}")
        
        result = response.json()
        return [item["embedding"] for item in result["data"]]
    
    def _custom_embed_with_batch(self, texts: list[str], batch_size: int = 10) -> list[list[float]]:
        """自定义 API 格式，支持分批处理
        
        Args:
            texts: 文本列表
            batch_size: 每批处理的文本数量
            
        Returns:
            embedding 向量列表
        """
        if not texts:
            return []
        
        all_embeddings = []
        
        # 分批处理
        for i in range(0, len(texts), batch_size):
            batch = texts[i:i + batch_size]
            batch_embeddings = self._custom_embed(batch)
            all_embeddings.extend(batch_embeddings)
        
        return all_embeddings
    
    def _custom_embed(self, texts: list[str]) -> list[list[float]]:
        """自定义 API 格式 (可重写适配不同 API)"""
        # 默认使用 OpenAI 兼容格式，大多数 API 都支持
        return self._openai_embed(texts)
    
    def embed_single(self, text: str) -> list[float]:
        """
        获取单个文本的 embedding
        
        Args:
            text: 单个文本
            
        Returns:
            embedding 向量
        """
        embeddings = self.embed([text])
        return embeddings[0] if embeddings else []
