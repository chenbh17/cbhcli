"""Skills管理器 - 技能注册、加载、使用"""
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
import json


@dataclass
class Skill:
    """技能定义"""
    name: str               # 技能名称（文件夹名）
    prompt: str             # skills.md 内容
    script_dir: Path        # script 文件夹路径
    base_dir: Path          # 技能根目录
    
    @property
    def has_scripts(self) -> bool:
        """是否有可执行脚本"""
        if not self.script_dir.exists():
            return False
        return any(self.script_dir.iterdir())
    
    def list_scripts(self) -> list[str]:
        """列出所有脚本文件"""
        if not self.script_dir.exists():
            return []
        return [f.name for f in sorted(self.script_dir.iterdir()) if f.is_file()]


class SkillManager:
    """技能管理器
    
    负责：
    - 扫描 agent 工作空间下的 skills 文件夹
    - 注册/加载/删除技能
    - 管理当前激活的技能列表
    """
    
    def __init__(self, workspace_path: Path):
        """
        Args:
            workspace_path: Agent 工作空间路径
        """
        self.workspace_path = workspace_path
        self.skills_dir = workspace_path / "skills"
        self._active_skills: list[str] = []  # 当前激活的技能名称列表
        self._skills_cache: dict[str, Skill] = {}  # 技能缓存
        
        # 确保 skills 目录存在
        self.skills_dir.mkdir(parents=True, exist_ok=True)
        
        # 加载已激活的技能配置
        self._load_active_config()
        
        # 初始扫描
        self.scan()
    
    def scan(self) -> dict[str, Skill]:
        """扫描 skills 文件夹，注册所有合法技能
        
        合法条件：技能文件夹下有 skills.md 和 script 文件夹
        
        Returns:
            已注册的技能字典 {name: Skill}
        """
        self._skills_cache.clear()
        
        if not self.skills_dir.exists():
            return {}
        
        for item in sorted(self.skills_dir.iterdir()):
            if not item.is_dir():
                continue
            
            skills_md = item / "skills.md"
            script_dir = item / "script"
            
            # 校验：必须有 skills.md 和 script 文件夹
            if skills_md.exists() and script_dir.exists():
                try:
                    prompt = skills_md.read_text(encoding='utf-8')
                    skill = Skill(
                        name=item.name,
                        prompt=prompt,
                        script_dir=script_dir,
                        base_dir=item
                    )
                    self._skills_cache[item.name] = skill
                except Exception:
                    pass  # 跳过无法读取的技能
        
        # 清理已激活但不再存在的技能
        self._active_skills = [
            s for s in self._active_skills if s in self._skills_cache
        ]
        self._save_active_config()
        
        return self._skills_cache
    
    def list_skills(self) -> list[Skill]:
        """列出所有已注册的技能
        
        Returns:
            技能列表
        """
        return list(self._skills_cache.values())
    
    def get_skill(self, name: str) -> Optional[Skill]:
        """获取指定技能
        
        Args:
            name: 技能名称
            
        Returns:
            Skill 或 None
        """
        return self._skills_cache.get(name)
    
    def create_skill(self, name: str, prompt_content: str, 
                     scripts: Optional[dict[str, str]] = None) -> Skill:
        """创建新技能
        
        Args:
            name: 技能名称
            prompt_content: skills.md 内容
            scripts: 可执行脚本 {filename: content}，可选
            
        Returns:
            创建的 Skill 对象
            
        Raises:
            ValueError: 技能已存在
        """
        skill_dir = self.skills_dir / name
        if skill_dir.exists():
            raise ValueError(f"技能 '{name}' 已存在")
        
        # 创建目录结构
        skill_dir.mkdir(parents=True)
        script_dir = skill_dir / "script"
        script_dir.mkdir()
        
        # 写入 skills.md
        skills_md = skill_dir / "skills.md"
        skills_md.write_text(prompt_content, encoding='utf-8')
        
        # 写入脚本文件
        if scripts:
            for filename, content in scripts.items():
                script_file = script_dir / filename
                script_file.write_text(content, encoding='utf-8')
                # 设置可执行权限
                script_file.chmod(0o755)
        
        # 注册到缓存
        skill = Skill(
            name=name,
            prompt=prompt_content,
            script_dir=script_dir,
            base_dir=skill_dir
        )
        self._skills_cache[name] = skill
        
        return skill
    
    def remove_skill(self, name: str) -> bool:
        """删除技能
        
        Args:
            name: 技能名称
            
        Returns:
            是否删除成功
        """
        import shutil
        
        skill = self._skills_cache.get(name)
        if not skill:
            return False
        
        # 从激活列表移除
        if name in self._active_skills:
            self._active_skills.remove(name)
            self._save_active_config()
        
        # 删除文件夹
        try:
            shutil.rmtree(skill.base_dir)
        except Exception:
            return False
        
        # 从缓存移除
        del self._skills_cache[name]
        return True
    
    def activate_skills(self, names: list[str]) -> list[str]:
        """激活指定技能（支持多选）
        
        Args:
            names: 要激活的技能名称列表
            
        Returns:
            实际激活的技能名称列表
        """
        activated = []
        for name in names:
            if name in self._skills_cache and name not in self._active_skills:
                self._active_skills.append(name)
                activated.append(name)
        
        self._save_active_config()
        return activated
    
    def deactivate_skill(self, name: str) -> bool:
        """取消激活指定技能
        
        Args:
            name: 技能名称
            
        Returns:
            是否成功
        """
        if name in self._active_skills:
            self._active_skills.remove(name)
            self._save_active_config()
            return True
        return False
    
    def deactivate_all(self):
        """取消所有激活的技能"""
        self._active_skills.clear()
        self._save_active_config()
    
    def get_active_skills(self) -> list[Skill]:
        """获取当前激活的技能列表
        
        Returns:
            激活的 Skill 列表
        """
        return [
            self._skills_cache[name]
            for name in self._active_skills
            if name in self._skills_cache
        ]
    
    def get_active_skill_names(self) -> list[str]:
        """获取当前激活的技能名称列表"""
        return list(self._active_skills)
    
    def is_active(self, name: str) -> bool:
        """判断技能是否已激活"""
        return name in self._active_skills
    
    def build_skills_prompt(self) -> str:
        """构建激活技能的系统提示内容
        
        将所有激活技能的 skills.md 内容拼接为系统提示
        
        Returns:
            拼接后的提示文本
        """
        active = self.get_active_skills()
        if not active:
            return ""
        
        parts = []
        for skill in active:
            parts.append(f"### 技能: {skill.name}\n{skill.prompt}")
            if skill.has_scripts:
                scripts = skill.list_scripts()
                script_path = str(skill.script_dir)
                parts.append(f"\n可用脚本（位于 {script_path}）:")
                for s in scripts:
                    parts.append(f"  - {s}")
                parts.append("")
        
        return "\n\n".join(parts)
    
    def _load_active_config(self):
        """从配置文件加载已激活的技能列表"""
        config_file = self.skills_dir / ".active_skills.json"
        if config_file.exists():
            try:
                data = json.loads(config_file.read_text(encoding='utf-8'))
                self._active_skills = data.get("active", [])
            except Exception:
                self._active_skills = []
    
    def _save_active_config(self):
        """保存已激活的技能列表到配置文件"""
        config_file = self.skills_dir / ".active_skills.json"
        try:
            config_file.write_text(
                json.dumps({"active": self._active_skills}, ensure_ascii=False, indent=2),
                encoding='utf-8'
            )
        except Exception:
            pass
