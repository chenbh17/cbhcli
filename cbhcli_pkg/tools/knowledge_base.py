"""知识库查询工具 - AI 可以查询知识库内容"""
from pathlib import Path
from cbhcli_pkg.tools.registry import BaseTool, ToolResult


class KnowledgeBaseTool(BaseTool):
    """知识库查询工具"""
    
    def __init__(self, vector_store=None, agent_manager=None, rerank_client=None, app=None):
        """
        初始化知识库工具
        
        Args:
            vector_store: 向量数据库实例
            agent_manager: Agent管理器
            rerank_client: 重排序客户端(可选)
            app: CBHCLI应用实例(用于获取当前Agent名称)
        """
        self.vector_store = vector_store
        self.agent_manager = agent_manager
        self.rerank_client = rerank_client
        self.app = app
    
    @property
    def name(self) -> str:
        return "knowledge_base"
    
    @property
    def description(self) -> str:
        return "查询Agent的知识库内容。可以搜索知识库中的文档、代码、笔记等。"
    
    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "搜索查询文本"
                },
                "top_k": {
                    "type": "integer",
                    "description": "返回结果数量(默认25)"
                }
            },
            "required": ["query"]
        }
    
    def execute(self, query: str, top_k: int = 25, agent_name: str = "") -> ToolResult:
        """
        执行知识库查询
        
        Args:
            query: 查询文本
            top_k: 返回结果数量
            agent_name: Agent名称（可选，不传则使用当前Agent）
            
        Returns:
            ToolResult: 查询结果
        """
        # 确保 top_k 是整数（AI可能传入字符串）
        top_k = int(top_k)
        # 自动获取当前 Agent 名称
        if not agent_name and self.app:
            agent_name = getattr(self.app, 'current_agent_name', '')
        
        if not agent_name:
            return ToolResult(
                success=False,
                output="",
                error="未指定 Agent 名称，且无法获取当前Agent"
            )
        
        # 检查向量数据库是否可用
        if self.vector_store is None:
            return ToolResult(
                success=False,
                output="",
                error="向量数据库未启用。请安装 chromadb: pip install chromadb"
            )
        
        try:
            # 执行向量搜索
            results = self.vector_store.query(agent_name, query, top_k=top_k)
            
            if not results:
                return ToolResult(
                    success=True,
                    output="知识库中未找到相关内容。"
                )
            
            # 如果有重排序模型，进行重排序
            if self.rerank_client and len(results) > 1:
                results = self._rerank_results(query, results)
            
            # 格式化结果
            output_lines = [f"📚 知识库查询结果 (查询: {query})\n"]
            
            for i, result in enumerate(results, 1):
                doc = result.get('document', '')
                metadata = result.get('metadata', {})
                score = result.get('score', result.get('distance', 0))
                file_name = metadata.get('file_name', '未知')
                file_type = metadata.get('file_type', '未知')
                
                output_lines.append(f"--- 结果 {i} [{file_type}] ---")
                output_lines.append(f"来源: {file_name}")
                output_lines.append(f"相关度: {score:.3f}")
                output_lines.append("")
                output_lines.append(doc)
                output_lines.append("")
            
            return ToolResult(
                success=True,
                output="\n".join(output_lines)
            )
            
        except Exception as e:
            return ToolResult(
                success=False,
                output="",
                error=f"知识库查询失败: {str(e)}"
            )
    
    def _rerank_results(self, query: str, results: list[dict]) -> list[dict]:
        """
        使用重排序模型对结果进行重排序
        
        Args:
            query: 查询文本
            results: 原始结果列表
            
        Returns:
            重排序后的结果列表
        """
        try:
            # 提取文档文本
            documents = [r.get('document', '') for r in results]
            
            # 调用重排序
            reranked = self.rerank_client.rerank(query, documents)
            
            # 将重排序结果映射回原始结果
            reranked_results = []
            for item in reranked:
                idx = item.get('index', 0)
                if idx < len(results):
                    new_result = results[idx].copy()
                    new_result['score'] = item.get('score', 0)
                    reranked_results.append(new_result)
            
            # 按分数降序排序
            reranked_results.sort(key=lambda x: x.get('score', 0), reverse=True)
            
            return reranked_results
        except Exception as e:
            print(f"重排序失败: {e}，使用原始结果")
            return results
