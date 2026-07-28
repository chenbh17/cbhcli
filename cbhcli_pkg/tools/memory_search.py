"""记忆搜索工具 - 语义搜索Agent历史对话"""
from pathlib import Path
from cbhcli_pkg.tools.registry import BaseTool, ToolResult


class MemorySearchTool(BaseTool):
    """记忆语义搜索工具"""
    
    def __init__(self, vector_store=None, agent_manager=None, app=None):
        """
        初始化记忆搜索工具
        
        Args:
            vector_store: 向量数据库实例(可选)
            agent_manager: Agent管理器实例(可选)
            app: CBHCLI应用实例(用于获取当前Agent名称)
        """
        self.vector_store = vector_store
        self.agent_manager = agent_manager
        self.app = app
    
    @property
    def name(self) -> str:
        return "memory_search"
    
    @property
    def description(self) -> str:
        return "语义搜索Agent的向量化知识内容（不包括对话历史）。可以搜索已记录的知识、笔记和文档。"
    
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
        执行语义搜索
        
        Args:
            query: 搜索查询
            top_k: 返回结果数量
            agent_name: Agent名称（可选，不传则使用当前Agent）
            
        Returns:
            ToolResult: 搜索结果
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
                error="未指定Agent名称，且无法获取当前Agent"
            )
        
        # 检查向量数据库是否可用
        if self.vector_store is None:
            # 降级方案: 从memory.md文件进行简单文本搜索
            return self._fallback_search(query, top_k, agent_name)
        
        try:
            # 执行语义搜索
            results = self.vector_store.query(agent_name, query, top_k)
            
            if not results:
                return ToolResult(
                    success=True,
                    output="未找到相关的向量化内容。(提示: 使用 /kb add 添加文件到知识库，或使用 /embedding index 索引工作空间)"
                )
            
            # 格式化结果
            output_lines = [f"🔍 搜索结果 (查询: {query})\n"]
            
            for i, result in enumerate(results, 1):
                output_lines.append(f"--- 结果 {i} ---")
                output_lines.append(result['document'])
                output_lines.append("")
            
            return ToolResult(
                success=True,
                output="\n".join(output_lines)
            )
            
        except Exception as e:
            return ToolResult(
                success=False,
                output="",
                error=f"搜索失败: {str(e)}"
            )
    
    def _fallback_search(self, query: str, top_k: int, agent_name: str) -> ToolResult:
        """
        降级搜索: 从memory.md文件进行简单关键词匹配
        
        Args:
            query: 搜索查询
            top_k: 返回结果数量
            agent_name: Agent名称
            
        Returns:
            ToolResult: 搜索结果
        """
        try:
            # 读取memory.md
            memory_file = Path.home() / f".cbhcli/agents/{agent_name}/memory.md"
            
            if not memory_file.exists():
                return ToolResult(
                    success=True,
                    output="未找到memory.md文件。"
                )
            
            content = memory_file.read_text(encoding='utf-8')
            
            # 简单段落匹配
            paragraphs = content.split('\n\n')
            
            # 计算匹配分数
            scored_paragraphs = []
            query_words = set(query.lower().split())
            
            for para in paragraphs:
                if not para.strip():
                    continue
                
                para_words = set(para.lower().split())
                # 计算交集
                match_count = len(query_words & para_words)
                
                if match_count > 0:
                    scored_paragraphs.append((match_count, para))
            
            # 排序并取top_k
            scored_paragraphs.sort(reverse=True, key=lambda x: x[0])
            top_results = scored_paragraphs[:top_k]
            
            if not top_results:
                return ToolResult(
                    success=True,
                    output="未找到匹配的向量化内容。(提示: 使用 /kb add 添加文件到知识库)"
                )
            
            # 格式化结果
            output_lines = [f"🔍 搜索结果 (查询: {query})\n"]
            output_lines.append("[向量搜索 - 仅查询向量化内容]\n")
            
            for i, (score, para) in enumerate(top_results, 1):
                output_lines.append(f"--- 结果 {i} (匹配度: {score}) ---")
                output_lines.append(para)
                output_lines.append("")
            
            return ToolResult(
                success=True,
                output="\n".join(output_lines)
            )
            
        except Exception as e:
            return ToolResult(
                success=False,
                output="",
                error=f"搜索失败: {str(e)}"
            )
