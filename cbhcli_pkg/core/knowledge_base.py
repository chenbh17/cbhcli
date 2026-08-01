"""知识库管理 - Agent知识库系统"""
from pathlib import Path
from typing import Optional
import uuid


class KnowledgeBase:
    """Agent知识库管理
    
    功能:
    - 管理 Agent 知识库目录
    - 添加/删除/列出知识文件
    - 索引知识库内容到向量数据库
    """
    
    def __init__(self, agent_name: str, vector_store=None, indexer=None):
        """
        初始化知识库
        
        Args:
            agent_name: Agent 名称
            vector_store: 向量数据库实例
            indexer: 记忆索引器实例
        """
        self.agent_name = agent_name
        self.vector_store = vector_store
        self.indexer = indexer
        self.kb_dir = Path.home() / f".cbhcli/agents/{agent_name}/knowledge"
        self.kb_dir.mkdir(parents=True, exist_ok=True)
    
    def add_file(self, file_path: str) -> dict:
        """
        添加文件到知识库
        
        Args:
            file_path: 文件路径(可以是绝对路径或相对路径)
            
        Returns:
            添加结果 {success, message, segments}
        """
        source_path = Path(file_path)
        
        if not source_path.exists():
            return {"success": False, "message": f"文件不存在: {file_path}"}
        
        if not source_path.is_file():
            return {"success": False, "message": f"不是文件: {file_path}"}
        
        # 复制到知识库目录
        dest_path = self.kb_dir / source_path.name
        
        # 处理重名
        if dest_path.exists():
            counter = 1
            while dest_path.exists():
                dest_path = self.kb_dir / f"{source_path.stem}_{counter}{source_path.suffix}"
                counter += 1
        
        try:
            import shutil
            shutil.copy2(source_path, dest_path)
            
            # 索引到向量数据库
            segments = self._index_file(dest_path)
            
            return {
                "success": True,
                "message": f"已添加文件: {dest_path.name}",
                "file": str(dest_path),
                "segments": segments
            }
        except Exception as e:
            return {"success": False, "message": f"添加文件失败: {str(e)}"}
    
    def remove_file(self, file_name: str) -> dict:
        """
        从知识库删除文件
        
        Args:
            file_name: 文件名
            
        Returns:
            删除结果
        """
        file_path = self.kb_dir / file_name
        
        if not file_path.exists():
            return {"success": False, "message": f"文件不存在: {file_name}"}
        
        try:
            file_path.unlink()
            
            # 从向量数据库删除 (简化处理：重新索引整个知识库)
            # 实际应用中应该按 document ID 删除
            
            return {
                "success": True,
                "message": f"已删除文件: {file_name}"
            }
        except Exception as e:
            return {"success": False, "message": f"删除文件失败: {str(e)}"}
    
    def list_files(self) -> list[dict]:
        """
        列出知识库中的所有文件
        
        Returns:
            文件列表 [{name, path, size}]
        """
        if not self.kb_dir.exists():
            return []
        
        files = []
        for f in self.kb_dir.iterdir():
            if f.is_file():
                files.append({
                    "name": f.name,
                    "path": str(f),
                    "size": f.stat().st_size
                })
        
        return files
    
    def reindex_all(self) -> dict:
        """
        重新索引整个知识库
        
        Returns:
            索引结果
        """
        if not self.kb_dir.exists():
            return {"success": False, "message": "知识库目录不存在"}
        
        total_segments = 0
        indexed_files = 0
        
        for f in self.kb_dir.iterdir():
            if f.is_file():
                segments = self._index_file(f)
                if segments > 0:
                    total_segments += segments
                    indexed_files += 1
        
        return {
            "success": True,
            "message": f"已索引 {indexed_files} 个文件，共 {total_segments} 个段落",
            "files": indexed_files,
            "segments": total_segments
        }
    
    def _index_file(self, file_path: Path) -> int:
        """
        索引单个文件到向量数据库
        
        Args:
            file_path: 文件路径
            
        Returns:
            索引的段落数
        """
        if not self.vector_store or not self.indexer:
            return 0
        
        try:
            # 读取文件内容
            if file_path.suffix in ['.md', '.txt', '.py', '.js', '.json', '.yaml', '.yml']:
                content = file_path.read_text(encoding='utf-8')
            else:
                # 跳过二进制文件
                return 0
            
            # 按段落分割
            paragraphs = [p.strip() for p in content.split('\n\n') if p.strip()]
            
            if not paragraphs:
                return 0
            
            # 生成文档 ID
            doc_ids = [
                f"kb_{self.agent_name}_{file_path.name}_{i}"
                for i in range(len(paragraphs))
            ]
            
            metadata = [
                {
                    "agent_name": self.agent_name,
                    "file_name": file_path.name,
                    "file_path": str(file_path),
                    "segment_index": i,
                    "type": "knowledge_base"
                }
                for i in range(len(paragraphs))
            ]
            
            # 添加到向量数据库
            self.vector_store.add_documents(
                agent_name=self.agent_name,
                texts=paragraphs,
                ids=doc_ids,
                metadata=metadata
            )
            
            return len(paragraphs)
        except Exception as e:
            print(f"索引文件 {file_path} 失败: {e}")
            return 0
