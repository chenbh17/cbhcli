"""Agent管理 - Agent配置和工作空间"""
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional
import json

from cbhcli_pkg import __version__


# 性格模板
SOUL_TEMPLATE = """# 性格

## 基本设定
- 你是一个有用的AI助手
- 诚实、专业、注重安全
- 在执行可能危险的操作前会提醒用户

## 沟通风格
- 简洁明了，避免冗余
- 技术准确，但易于理解
- 适当使用emoji增加亲和力

## 行为准则
- 优先保证系统安全
- 在执行破坏性操作前要求确认
- 遇到不确定的情况，坦诚告知用户
- 提供多种方案供用户选择

## 个性化设定
在此添加Agent的个性化特征，例如：
- 特定的专业领域偏好
- 特殊的沟通习惯
- 个人风格特征

## 更新记录
- 初始创建
"""


# 工具使用指南模板
TOOLS_TEMPLATE = """# 工具使用指南

## 核心工作流程（必须遵守！）

### 1. 每个任务必须先用 Todo 工具做规划
无论任务简单还是复杂，收到用户请求后第一步都是调用 Todo 工具创建任务计划：
- 将任务拆分为清晰的步骤
- 每个步骤设为 pending 状态
- 开始执行某步骤前标记为 in_progress
- 完成后标记为 completed
- 每次调用 Todo 都传入完整列表（所有条目及最新状态）

### 2. 使用 edit 工具前必须先用 read 工具读取文件
**禁止在未读取文件的情况下直接使用 edit 工具！**
- edit 的 old_str 必须与文件实际内容完全一致（包括缩进和空白）
- 正确流程：先 read 读取文件 → 确认要修改的内容 → 再 edit 替换

## 工具调用说明
所有工具的详细参数定义通过 API 的 Function Calling 协议自动获取，你只需根据参数 schema 正确传参即可。
MCP 扩展工具名称格式为 `mcp_服务器名_工具名`，使用方式与内置工具完全相同。

## 后台任务管理（terminal 超时）
terminal 命令超过 timeout(默认30秒) 未完成时进程**不会被终止**，而是转为后台任务继续运行：
- **立即使用 process 工具**（task_id）实时监控进度，等待任务完成并获取全部输出
- 监控期间用户可 Ctrl+C 停止监控（任务仍继续运行）
- 任务运行满 1 小时将自动终止
- 用户要求终止或任务失控时，使用 kill_process 工具（task_id）手动终止
- 不传参数的 process 工具可列出所有后台任务

## 最佳实践
- **每个任务第一步调用 Todo 工具创建计划**，然后按计划逐步执行
- **edit 前必须先 read**，确认文件内容后再精确替换
- 使用 grep/glob 快速定位文件和内容，避免盲目读取大量文件
- 在需求不明确时使用 ask_user 向用户确认，而不是猜测
- 重要操作前提醒用户
- 出错时提供解决方案
- **需要识别图片时使用 image 工具**，传入图片路径和识别需求；当前主模型支持视觉时图片会直接发送到会话中由你识别，否则工具自动调用其他视觉模型识别
- **有多个相互独立的子任务时使用 delegate_task 传入 tasks 列表并行委托**，全部子Agent完成后主Agent再继续，可显著缩短总耗时
"""


# 对话记录模板
MEMORY_TEMPLATE = """# 对话记录

## 使用说明
本文件用于保存需要长期记住的重要信息。
**只有当用户明确要求记录时，才将内容写入本文件。**
普通对话历史不会自动保存到这里，而是通过向量存储进行语义搜索。

---

"""


# CBHCLI使用说明 - 每个agent都应知道
CBHCLI_USAGE_GUIDE = """
# CBHCLI 使用说明

## 基本信息
CBHCLI 是一个AI驱动的终端助手，帮助你执行各种任务。
所有工具通过 OpenAI Function Calling 协议自动调用，无需手动输入调用格式。

## 斜杠命令（非常重要！必读！）
**核心原则：斜杠命令是用户自己在对话中输入的，不是通过工具执行的！**

当用户询问如何使用某个功能时，你必须：
1. 查阅本文件中的命令说明
2. 准确告知用户应输入什么命令
3. 不要编造命令格式或步骤
4. 不要用工具执行斜杠命令

常用命令：
- /agent [add|rm|use] <name> - Agent管理
- /model [add|use|rm|config|embedding|rerank] - 模型管理
- /new 或 /reset - 创建新会话（自动保存当前会话到history）
- /resume [编号] - 列出或恢复历史会话
- /history - 查看历史会话列表
- /ctx - 查看上下文使用情况
- /comp [指令] - 手动压缩上下文（可带保留/丢弃指令）
- /mode [readonly|standard|auto|yolo] - 权限模式切换（Shift+Tab循环切换）
- /permissions [list|add|rm] - 权限规则管理
- /hooks [list|reload|test] - 生命周期钩子管理
- /undo [ID|list] - 回滚write/edit的文件修改
- /embedding [index|status|clear|reindex] - 向量索引管理
- /kb [add|list|rm|reindex|status] - 知识库管理
- /skills [list|add|use|off|rm] - 技能管理
- /tools [list|on|off] - 工具开关管理
- /fallback [add|list|rm|reorder|clear] - 备用模型管理
- /mcp [add|list|rm|refresh|tools|on|off] - MCP服务器管理
- quit - 退出程序

## 权限模式（Harness 治理层）
cbhcli 有四档权限模式，用户按 Shift+Tab 循环切换，或用 /mode 命令直接设置：
- readonly 只读模式：你只能查看/分析，一切修改操作被系统拒绝
- standard 标准模式（默认）：危险操作逐个确认，红线操作（rm -rf /、写 .env 等）被禁止
- auto 自动模式：工作目录内写操作和常见开发命令自动放行，红线仍禁止
- yolo 最高权限：全部操作零确认直接执行（红线仅警告）
工具调用被权限规则拒绝时，错误信息会说明原因，请换其他方式完成任务或请用户切换模式，不要反复重试同一被拒绝的操作。

## 工作空间
位于: ~/.cbhcli/agents/<agent_name>/
- config.json: Agent配置 | soul.md: 性格 | tools.md: 工具规则
- memory.md: 长期记忆（始终在系统提示中，不索引到向量库）
- usage.md: 使用说明(本文件) | history/: 会话历史
- knowledge/: 知识库 | skills/: 技能目录

## 会话历史管理
- /new 或 /reset 创建新会话时，当前会话自动保存到 history/
- /history 或 /resume 查看和恢复历史会话

## memory.md 长期记忆
- 只有用户明确要求"记住"时才写入，普通对话不自动保存
- 始终包含在系统提示中

## 技能系统
技能是可复用的提示词+可选脚本，存放在 skills/ 目录下。
- /skills list - 列出 | /skills add - 交互式创建
- /skills use - 激活（支持多选） | /skills off - 取消激活
- /skills rm <name> - 删除
也可直接告诉AI创建技能，AI使用 skills_create 工具自动创建。

## 知识库系统
- /kb add <file> - 添加文件 | /kb list - 列出
- /kb rm - 删除 | /kb reindex - 重建索引 | /kb status - 状态
- 使用 knowledge_base 工具查询知识库内容

## 向量搜索功能
要启用语义搜索：
1. `/model embedding add` - 配置嵌入模型（按提示输入名称/API Key/Base URL/模型ID/类型）
   常用: OpenAI(text-embedding-3-small) | 智谱(embedding-2) | 通义千问(text-embedding-v3)
2. `/embedding index` - 手动触发索引（配置后必须执行此步骤）
可选：`/model rerank add` 配置重排序模型提高搜索质量。

## MCP 工具服务器管理
MCP (Model Context Protocol) 允许连接外部工具服务器，扩展工具能力。

**重要原则：MCP命令由用户直接输入，AI不要用工具执行！**

命令参考：
- /mcp add <名称> <URL> [header名=值 ...] - 添加服务器
- /mcp list - 列出所有 | /mcp rm <名称> - 移除
- /mcp refresh <名称> - 重连刷新 | /mcp tools <名称> - 查看工具
- /mcp on|off <服务器> <工具名> - 启用/禁用工具

添加后工具自动注册，名称格式为 mcp_服务器名_工具名。

## 备用模型管理
当主模型断网或异常时，自动切换到备用模型继续任务。视觉模型同理。
- /fallback list - 查看备用模型配置
- /fallback add [main|vision] <模型名> - 添加备用模型
- /fallback rm [main|vision] <模型名> - 移除备用模型
- /fallback reorder [main|vision] - 重新排序备用模型
- /fallback clear [main|vision] - 清空备用模型列表
main=主模型备用, vision=视觉模型备用(image工具使用)。

## 记录信息
当用户要求记录信息时：
1. 判断类型(记忆/知识/技能)
2. 长期记忆 → 追加到 memory.md（用户明确要求时）
3. 知识文件 → 保存到 knowledge/
4. 使用 write/edit 追加，不覆盖

## 注意事项
- 文件操作使用绝对路径
- 记录信息追加到文件末尾
- 用户问如何使用功能时，告知命令即可，不要用工具执行
"""


@dataclass
class AgentConfig:
    """Agent配置"""
    name: str
    workspace_path: Path
    primary_model: Optional[str] = None
    description: str = ""
    context_limit_ratio: float = 0.8
    auto_compress: bool = True
    max_tool_calls: int = 100
    disabled_tools: list = field(default_factory=list)  # 被禁用的工具名称列表
    config_version: str = ""  # 配置版本号，用于迁移判断
    created_at: datetime = field(default_factory=datetime.now)

    # 4.7.5 新增：cbhpacks数据科学工具默认关闭列表
    DEFAULT_DISABLED_CBHPACKS = [
        "cbhpacks_bins_model", "cbhpacks_binary_model", "cbhpacks_uns_model",
        "cbhpacks_linear_model", "cbhpacks_cols_select", "cbhpacks_cols_select_js",
        "cbhpacks_cols_encode", "cbhpacks_cols_operate", "cbhpacks_desc_df",
        "cbhpacks_desc_col", "cbhpacks_con_sql", "cbhpacks_con_linux",
        "cbhpacks_get_random_data",
    ]

    def to_dict(self) -> dict:
        """转换为字典"""
        return {
            "name": self.name,
            "description": self.description,
            "primary_model": self.primary_model,
            "context_limit_ratio": self.context_limit_ratio,
            "auto_compress": self.auto_compress,
            "max_tool_calls": self.max_tool_calls,
            "disabled_tools": self.disabled_tools,
            "config_version": self.config_version,
            "created_at": self.created_at.isoformat()
        }

    @classmethod
    def from_dict(cls, data: dict, workspace_path: Path) -> 'AgentConfig':
        """从字典创建，含自动迁移逻辑"""
        disabled = data.get("disabled_tools", [])
        config_version = data.get("config_version", "")

        # 迁移：旧版Agent（无config_version）且disabled_tools为空 → 自动关闭cbhpacks工具
        if not config_version and not disabled:
            disabled = list(cls.DEFAULT_DISABLED_CBHPACKS)

        return cls(
            name=data["name"],
            workspace_path=workspace_path,
            primary_model=data.get("primary_model"),
            description=data.get("description", ""),
            context_limit_ratio=data.get("context_limit_ratio", 0.8),
            auto_compress=data.get("auto_compress", True),
            max_tool_calls=data.get("max_tool_calls", 100),
            disabled_tools=disabled,
            config_version=config_version or __version__,
            created_at=datetime.fromisoformat(data["created_at"]) if "created_at" in data else datetime.now()
        )


@dataclass
class AgentPersona:
    """Agent人格配置(从MD文件加载)"""
    soul: str = ""
    tools_description: str = ""
    memory: str = ""
    usage: str = ""

    def build_system_prompt(self, agent_name: str = "", model_name: str = "",
                            memory_content: str = "",
                            active_skills_prompt: str = "",
                            cwd: str = "",
                            supports_vision: bool = False) -> str:
        """
        构建系统提示

        Args:
            agent_name: Agent名称
            model_name: 当前使用的模型名称
            memory_content: memory.md 文件内容（长期记忆）
            active_skills_prompt: 已激活技能的提示内容
            cwd: 用户当前工作目录

        Returns:
            完整的系统提示
        """
        parts = []

        # 基本信息 - 放在最前面
        parts.append("## 基本信息")
        if agent_name:
            parts.append(f"- 你的名称: {agent_name}")
        if model_name:
            parts.append(f"- 当前使用的模型: {model_name}")
        if cwd:
            parts.append(f"- 用户当前工作目录: {cwd}")
            parts.append(f"- 重要：用户的所有任务默认在此目录下进行，文件操作请使用此目录作为基准路径")
        if supports_vision:
            parts.append(f"- 视觉能力: ✅ 你是一个支持视觉的多模态模型，可以识别和分析图片内容")
            parts.append(f"- 图片识别方式: 调用 image 工具识别图片时，图片会以多模态消息直接发送到当前会话，你可以直接查看并分析图片内容")
        parts.append("")

        # 长期记忆（来自 memory.md）- 始终包含
        if memory_content:
            parts.append(f"## 长期记忆（重要！）\n以下是用户要求你记住的重要信息：\n{memory_content}\n")

        # 使用说明放在最前面
        if self.usage:
            parts.append(f"## 使用说明\n{self.usage}\n")

        # 已激活的技能（来自 skills/ 目录）
        if active_skills_prompt:
            parts.append(f"## 激活的技能\n{active_skills_prompt}\n")

        if self.soul:
            parts.append(f"## 性格\n{self.soul}\n")

        if self.tools_description:
            parts.append(f"## 工具使用指南\n{self.tools_description}\n")

        return "\n".join(parts)


class AgentManager:
    """Agent管理器"""
    
    def __init__(self, workspace_base: Path):
        """
        初始化Agent管理器
        
        Args:
            workspace_base: Agent工作空间根目录
        """
        self.workspace_base = workspace_base
        self.workspace_base.mkdir(parents=True, exist_ok=True)
    
    def create_agent(self, name: str, description: str = "", 
                     primary_model: Optional[str] = None) -> AgentConfig:
        """
        创建新Agent
        
        Args:
            name: Agent名称
            description: 描述
            primary_model: 首选模型名称
            
        Returns:
            AgentConfig: Agent配置
        """
        workspace_path = self.workspace_base / name
        
        # 创建工作空间目录
        workspace_path.mkdir(parents=True, exist_ok=True)
        
        # 创建知识库目录
        knowledge_dir = workspace_path / "knowledge"
        knowledge_dir.mkdir(exist_ok=True)
        
        # 创建 skills 目录
        skills_dir = workspace_path / "skills"
        skills_dir.mkdir(exist_ok=True)
        
        # 创建配置文件
        config = AgentConfig(
            name=name,
            workspace_path=workspace_path,
            primary_model=primary_model,
            description=description,
            disabled_tools=list(AgentConfig.DEFAULT_DISABLED_CBHPACKS),
            config_version=__version__
        )
        
        self._save_config(config)
        
        # 创建MD文件
        self._create_md_file(workspace_path / "soul.md", SOUL_TEMPLATE)
        self._create_md_file(workspace_path / "tools.md", TOOLS_TEMPLATE)
        self._create_md_file(workspace_path / "memory.md", MEMORY_TEMPLATE)
        self._create_md_file(workspace_path / "usage.md", CBHCLI_USAGE_GUIDE)

        return config
    
    def load_agent(self, name: str) -> Optional[AgentConfig]:
        """
        加载Agent配置
        
        Args:
            name: Agent名称
            
        Returns:
            AgentConfig或None
        """
        workspace_path = self.workspace_base / name
        config_file = workspace_path / "config.json"
        
        if not config_file.exists():
            return None
        
        with open(config_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        return AgentConfig.from_dict(data, workspace_path)
    
    def load_agent_persona(self, name: str) -> AgentPersona:
        """
        加载Agent人格配置

        Args:
            name: Agent名称

        Returns:
            AgentPersona
        """
        workspace_path = self.workspace_base / name

        persona = AgentPersona()

        # 读取使用说明
        usage_file = workspace_path / "usage.md"
        if usage_file.exists():
            persona.usage = usage_file.read_text(encoding='utf-8')
        else:
            persona.usage = CBHCLI_USAGE_GUIDE

        # 读取MD文件
        soul_file = workspace_path / "soul.md"
        if soul_file.exists():
            persona.soul = soul_file.read_text(encoding='utf-8')

        tools_file = workspace_path / "tools.md"
        if tools_file.exists():
            persona.tools_description = tools_file.read_text(encoding='utf-8')

        memory_file = workspace_path / "memory.md"
        if memory_file.exists():
            persona.memory = memory_file.read_text(encoding='utf-8')

        return persona
    
    def list_agents(self) -> list[AgentConfig]:
        """
        列出所有Agent
        
        Returns:
            Agent配置列表
        """
        agents = []
        
        if not self.workspace_base.exists():
            return agents
        
        for item in self.workspace_base.iterdir():
            if item.is_dir() and (item / "config.json").exists():
                config = self.load_agent(item.name)
                if config:
                    agents.append(config)
        
        return agents
    
    def delete_agent(self, name: str) -> bool:
        """
        删除Agent
        
        Args:
            name: Agent名称
            
        Returns:
            是否成功删除
        """
        import shutil
        
        workspace_path = self.workspace_base / name
        
        if not workspace_path.exists():
            return False
        
        shutil.rmtree(workspace_path)
        return True
    
    def switch_agent(self, name: str) -> Optional[AgentConfig]:
        """
        切换到指定Agent
        
        Args:
            name: Agent名称
            
        Returns:
            AgentConfig或None
        """
        return self.load_agent(name)
    
    def _save_config(self, config: AgentConfig) -> None:
        """保存Agent配置"""
        config_file = config.workspace_path / "config.json"
        with open(config_file, 'w', encoding='utf-8') as f:
            json.dump(config.to_dict(), f, indent=2, ensure_ascii=False)
    
    def _create_md_file(self, file_path: Path, content: str) -> None:
        """创建MD文件"""
        if not file_path.exists():
            file_path.write_text(content, encoding='utf-8')
    
    def update_memory(self, agent_name: str, memory_content: str) -> None:
        """
        更新Agent记忆
        
        Args:
            agent_name: Agent名称
            memory_content: 记忆内容(会追加到文件)
        """
        memory_file = self.workspace_base / agent_name / "memory.md"
        
        with open(memory_file, 'a', encoding='utf-8') as f:
            f.write(memory_content + "\n\n")
