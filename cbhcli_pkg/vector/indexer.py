"""记忆索引器 - 将 memory.md 和其他文件索引到向量数据库"""
from pathlib import Path
from typing import Optional
from cbhcli_pkg.vector.store import VectorStore


class MemoryIndexer:
    """记忆索引器
    
    负责索引:
    - memory.md (对话记忆)
    - soul.md (性格)
    - tools.md (工具指南)
    - usage.md (使用说明)
    - knowledge/ 目录下的所有文件
    - skills/ 目录下各技能的 skills.md
    """
    
    # 需要索引的 Agent 工作空间文件
    AGENT_FILES = [
        "memory.md",
        "soul.md",
        "tools.md",
        "usage.md",
    ]
    
    def __init__(self, vector_store: VectorStore):
        """
        初始化记忆索引器
        
        Args:
            vector_store: 向量数据库
        """
        self.vector_store = vector_store
    
    def index_agent_workspace(self, agent_name: str, workspace_path: Path) -> int:
        """
        索引 Agent 工作空间的所有 md 文件
        
        Args:
            agent_name: Agent名称
            workspace_path: 工作空间路径
            
        Returns:
            索引的段落总数
        """
        # 先删除旧集合，确保内容更新后能正确索引
        # 因为段落 ID 是基于序号而非内容哈希，内容变化后 ID 不变
        self.vector_store.delete_collection(agent_name)
        
        total_segments = 0
        
        # 索引标准 md 文件
        for md_file_name in self.AGENT_FILES:
            md_file = workspace_path / md_file_name
            if md_file.exists():
                segments = self._index_file(agent_name, md_file, file_type="agent_md")
                total_segments += segments
        
        # 索引知识库目录
        kb_dir = workspace_path / "knowledge"
        if kb_dir.exists() and kb_dir.is_dir():
            for f in kb_dir.iterdir():
                if f.is_file() and f.suffix in ['.md', '.txt', '.py', '.js', '.json', '.yaml', '.yml']:
                    segments = self._index_file(agent_name, f, file_type="knowledge_base")
                    total_segments += segments
        
        # 索引 skills 目录下各技能的 skills.md
        skills_dir = workspace_path / "skills"
        if skills_dir.exists() and skills_dir.is_dir():
            for skill_dir in skills_dir.iterdir():
                if skill_dir.is_dir():
                    skill_md = skill_dir / "skills.md"
                    if skill_md.exists():
                        segments = self._index_file(agent_name, skill_md, file_type="skill")
                        total_segments += segments
        
        return total_segments
    
    def index_memory_file(self, agent_name: str, memory_file: Path) -> int:
        """
        索引memory.md文件(向后兼容)
        
        Args:
            agent_name: Agent名称
            memory_file: memory.md文件路径
            
        Returns:
            索引的段落数
        """
        if not memory_file.exists():
            return 0
        
        return self._index_file(agent_name, memory_file, file_type="memory")
    
    def _index_file(self, agent_name: str, file_path: Path, file_type: str = "agent_md") -> int:
        """
        索引单个文件
        
        Args:
            agent_name: Agent名称
            file_path: 文件路径
            file_type: 文件类型标签
            
        Returns:
            索引的段落数
        """
        if not file_path.exists():
            return 0
        
        # 读取文件
        try:
            content = file_path.read_text(encoding='utf-8')
        except Exception:
            return 0
        
        # 按段落分割
        paragraphs = content.split('\n\n')
        
        # 过滤空段落
        paragraphs = [p.strip() for p in paragraphs if p.strip()]
        
        if not paragraphs:
            return 0
        
        # 准备文档和ID
        file_stem = file_path.stem
        texts = paragraphs
        ids = [f"{agent_name}_{file_stem}_{i}" for i in range(len(paragraphs))]
        metadata = [
            {
                "agent_name": agent_name, 
                "file_name": file_path.name,
                "file_type": file_type,
                "segment_index": i,
            }
            for i in range(len(paragraphs))
        ]
        
        # 添加到向量数据库
        self.vector_store.add_documents(agent_name, texts, ids, metadata)
        
        return len(paragraphs)
    
    def add_memory(self, text: str, agent_name: str = "", metadata: dict = None) -> None:
        """
        添加单条记忆到向量数据库
        
        Args:
            text: 记忆文本
            agent_name: Agent名称(用于确定存储集合)
            metadata: 元数据(可选)
        """
        import uuid
        
        # 生成唯一ID
        doc_id = f"memory_{uuid.uuid4().hex[:8]}"
        
        # 确保metadata非空
        if metadata is None:
            metadata = {"type": "conversation"}
        
        # 添加到向量数据库
        self.vector_store.add_documents(
            agent_name=agent_name,
            texts=[text],
            ids=[doc_id],
            metadata=[metadata]
        )
    
    def update_index(self, agent_name: str, memory_file: Path) -> int:
        """
        更新索引(删除旧索引并重新索引)
        
        Args:
            agent_name: Agent名称
            memory_file: memory.md文件路径
            
        Returns:
            索引的段落数
        """
        # 删除旧集合
        self.vector_store.delete_collection(agent_name)
        
        # 重新索引
        return self.index_memory_file(agent_name, memory_file)
