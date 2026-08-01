"""重排序服务 - 对搜索结果进行重排序"""
import requests
from typing import Optional


class RerankClient:
    """重排序模型客户端
    
    支持:
    - Jina Reranker API
    - Cohere Rerank API  
    - 通义千问 Rerank
    - 其他兼容 API
    """
    
    def __init__(self, config: dict):
        """
        初始化重排序客户端
        
        Args:
            config: 配置字典 {name, apiKey, url, model, top_n}
        """
        self.name = config.get("name", "rerank")
        self.base_url = config.get("url", "https://api.jina.ai/v1").rstrip('/')
        self.api_key = config["apiKey"]
        self.model = config.get("model", "jina-reranker-v2-base-multilingual")
        self.top_n = config.get("top_n", 5)
        
        self._session = requests.Session()
        self._session.headers.update({
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        })
    
    def rerank(self, query: str, documents: list[str], top_n: Optional[int] = None) -> list[dict]:
        """
        对文档列表进行重排序
        
        Args:
            query: 查询文本
            documents: 待排序的文档列表
            top_n: 返回前 N 个结果 (默认使用配置的 top_n)
            
        Returns:
            排序后的结果列表 [{index, document, score}]
        """
        if not documents:
            return []
        
        top_n = top_n or self.top_n
        
        # 根据 API 类型选择调用方式
        if "jina" in self.model.lower():
            return self._jina_rerank(query, documents, top_n)
        elif "cohere" in self.model.lower():
            return self._cohere_rerank(query, documents, top_n)
        else:
            # 默认使用 Jina 兼容格式
            return self._jina_rerank(query, documents, top_n)
    
    def _jina_rerank(self, query: str, documents: list[str], top_n: int) -> list[dict]:
        """Jina Reranker API 格式"""
        payload = {
            "model": self.model,
            "query": query,
            "documents": documents,
            "top_n": top_n
        }
        
        response = self._session.post(
            f"{self.base_url}/rerank",
            json=payload,
            timeout=30
        )
        
        if response.status_code != 200:
            raise Exception(f"Rerank 请求失败: {response.status_code} - {response.text}")
        
        result = response.json()
        
        # 格式化结果
        formatted_results = []
        for item in result.get("results", []):
            formatted_results.append({
                "index": item.get("index"),
                "document": item.get("document", {}).get("text", documents[item.get("index", 0)]),
                "score": item.get("relevance_score", 0)
            })
        
        return formatted_results
    
    def _cohere_rerank(self, query: str, documents: list[str], top_n: int) -> list[dict]:
        """Cohere Rerank API 格式"""
        payload = {
            "model": self.model,
            "query": query,
            "documents": documents,
            "top_n": top_n
        }
        
        response = self._session.post(
            f"{self.base_url}/v1/rerank",
            json=payload,
            timeout=30
        )
        
        if response.status_code != 200:
            raise Exception(f"Rerank 请求失败: {response.status_code} - {response.text}")
        
        result = response.json()
        
        # 格式化结果
        formatted_results = []
        for item in result.get("results", []):
            idx = item.get("index", 0)
            formatted_results.append({
                "index": idx,
                "document": documents[idx] if idx < len(documents) else "",
                "score": item.get("relevance_score", 0)
            })
        
        return formatted_results
