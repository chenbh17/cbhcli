"""向量数据库 - ChromaDB封装，支持自定义嵌入模型API"""
import os
# 禁用ChromaDB遥测
os.environ["ANONYMIZED_TELEMETRY"] = "False"
os.environ["CHROMA_TELEMETRY"] = "False"

from pathlib import Path
from typing import Optional, List
import json


class APIEmbeddingFunction:
    """使用外部API的嵌入函数"""
    
    def __init__(self, embedding_client):
        """
        初始化
        
        Args:
            embedding_client: 嵌入模型客户端
        """
        self._client = embedding_client
    
    def __call__(self, input: List[str]) -> List[List[float]]:
        """
        计算嵌入向量
        
        Args:
            input: 文本列表
            
        Returns:
            嵌入向量列表
        """
        return self._client.embed(list(input))
    
    @staticmethod
    def name() -> str:
        """ChromaDB 1.5.x 要求的 name 方法"""
        return "cbhcli-custom-embedding"


class VectorStore:
    """向量数据库封装(使用ChromaDB)"""

    def __init__(self, persist_directory: Path, embedding_client=None):
        """
        初始化向量数据库

        Args:
            persist_directory: 持久化目录
            embedding_client: 自定义嵌入模型客户端(必须)
        """
        if embedding_client is None:
            raise ValueError(
                "VectorStore 需要 embedding_client 参数。\n"
                "请使用 /model embedding add 配置嵌入模型，\n"
                "或安装 chromadb 使用内置模型: pip install chromadb"
            )
        
        self.persist_directory = persist_directory
        self.persist_directory.mkdir(parents=True, exist_ok=True)
        self._client = None
        self._collections = {}
        self._embedding_client = embedding_client
        self._embedding_function = APIEmbeddingFunction(embedding_client)

        self._initialize_client()

    def _initialize_client(self):
        """初始化ChromaDB客户端"""
        try:
            import chromadb
            
            # 使用自定义嵌入函数创建客户端
            # 关键: 传递 embedding_function 参数
            self._client = chromadb.PersistentClient(
                path=str(self.persist_directory)
            )
        except ImportError as e:
            raise ImportError(
                f"ChromaDB未安装或导入失败: {e}。请运行: pip install chromadb"
            )
    
    def get_or_create_collection(self, agent_name: str):
        """
        获取或创建集合
        
        Args:
            agent_name: Agent名称(用作集合名)
            
        Returns:
            ChromaDB Collection
        """
        if agent_name not in self._collections:
            # 关键: 必须传递 embedding_function 使用自定义模型
            self._collections[agent_name] = self._client.get_or_create_collection(
                name=f"agent_{agent_name}",
                embedding_function=self._embedding_function,
                metadata={"description": f"Agent {agent_name}的记忆"}
            )
        
        return self._collections[agent_name]
    
    def add_documents(self, agent_name: str, texts: list[str],
                     ids: list[str], metadata: list[dict] = None) -> None:
        """
        添加文档

        Args:
            agent_name: Agent名称
            texts: 文本列表
            ids: ID列表
            metadata: 元数据列表(可选)
        """
        collection = self.get_or_create_collection(agent_name)

        # ChromaDB要求metadata必须是非空字典
        if metadata is None:
            metadata = [{"source": "cbhcli"} for _ in texts]
        else:
            metadata = [m if m else {"source": "cbhcli"} for m in metadata]

        # 预先计算嵌入向量，避免 ChromaDB 调用默认模型
        embeddings = self._embedding_client.embed(texts)
        
        collection.add(
            documents=texts,
            embeddings=embeddings,
            ids=ids,
            metadatas=metadata
        )
    
    def query(self, agent_name: str, query_text: str, top_k: int = 5) -> list[dict]:
        """
        语义查询
        
        Args:
            agent_name: Agent名称
            query_text: 查询文本
            top_k: 返回结果数量
            
        Returns:
            查询结果列表 [{document, metadata, distance}]
        """
        collection = self.get_or_create_collection(agent_name)
        
        # 预先计算查询向量，避免 ChromaDB 调用默认模型
        query_embedding = self._embedding_client.embed_single(query_text)
        
        results = collection.query(
            query_embeddings=[query_embedding],
            n_results=int(top_k),
            include=["documents", "metadatas", "distances"]
        )
        
        # 格式化结果
        formatted_results = []
        for i in range(len(results['documents'][0])):
            formatted_results.append({
                'document': results['documents'][0][i],
                'metadata': results['metadatas'][0][i] if results['metadatas'] else {},
                'distance': results['distances'][0][i] if results['distances'] else 0
            })
        
        return formatted_results
    
    def delete_collection(self, agent_name: str) -> None:
        """删除集合"""
        try:
            self._client.delete_collection(f"agent_{agent_name}")
            if agent_name in self._collections:
                del self._collections[agent_name]
        except Exception:
            pass
    
    def count(self, agent_name: str) -> int:
        """获取文档数量"""
        collection = self.get_or_create_collection(agent_name)
        return collection.count()
