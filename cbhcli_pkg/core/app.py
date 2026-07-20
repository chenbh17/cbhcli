"""主应用 - CBHCLIApp"""
import os
import json
from pathlib import Path
from typing import Optional

from cbhcli_pkg.core.input_box import ChatInputBox

from cbhcli_pkg.config.global_config import GlobalConfig
from cbhcli_pkg.core.agent import AgentManager, AgentConfig, AgentPersona
from cbhcli_pkg.core.session import Session, Message, ContextWindow
from cbhcli_pkg.core.session_history import SessionHistoryManager
from cbhcli_pkg.core.model import LLMClient
from cbhcli_pkg.core.subagent import SubAgentScheduler
from cbhcli_pkg.core.tool_executor import ToolExecutor
from cbhcli_pkg.core.ai_handler import AIHandler
from cbhcli_pkg.core.mcp_manager import MCPManager
from cbhcli_pkg.core.constants import (
    C_RESET, C_DIM, C_USER_BG, C_USER_FG, C_AI_HINT, C_SEP,
    DEFAULT_CONTEXT_LIMIT, DEFAULT_COMPRESSION_RATIO
)
from cbhcli_pkg.core.errors import ModelNotConfiguredError

from cbhcli_pkg.tools.registry import ToolRegistry
from cbhcli_pkg.tools.terminal import TerminalTool
from cbhcli_pkg.tools.file_read import ReadTool
from cbhcli_pkg.tools.file_write import WriteTool
from cbhcli_pkg.tools.file_edit import EditTool
from cbhcli_pkg.tools.memory_search import MemorySearchTool
from cbhcli_pkg.tools.knowledge_base import KnowledgeBaseTool
from cbhcli_pkg.tools.python_tool import PythonTool, remove_python_session
from cbhcli_pkg.tools.skills_create import SkillsCreateTool
from cbhcli_pkg.tools.delegate_task import DelegateTaskTool
from cbhcli_pkg.tools.grep import GrepTool
from cbhcli_pkg.tools.glob_tool import GlobTool
from cbhcli_pkg.tools.ask_user import AskUserQuestionTool
from cbhcli_pkg.tools.todo import TodoTool
from cbhcli_pkg.tools.image import ImageTool
from cbhcli_pkg.tools.process import ProcessTool
from cbhcli_pkg.tools.kill_process import KillProcessTool

# cbhpacks 数据科学工具
from cbhcli_pkg.tools.cbhpacks_bins import BinsModelTool
from cbhcli_pkg.tools.cbhpacks_training import BinaryModelTool, UnsModelTool, LinearModelTool
from cbhcli_pkg.tools.cbhpacks_select import ColsSelectTool, ColsSelectJsTool
from cbhcli_pkg.tools.cbhpacks_encode import ColsEncodeTool
from cbhcli_pkg.tools.cbhpacks_preprocess import ColsOperateTool, DescDfTool, DescColTool
from cbhcli_pkg.tools.cbhpacks_sql import ConSqlTool
from cbhcli_pkg.tools.cbhpacks_linux import ConLinuxTool
from cbhcli_pkg.tools.cbhpacks_data import GetRandomDataTool

from cbhcli_pkg.context.token_counter import get_token_counter
from cbhcli_pkg.context.compressor import ContextCompressor

from cbhcli_pkg.vector.store import VectorStore
from cbhcli_pkg.vector.indexer import MemoryIndexer

from cbhcli_pkg.core.embedding_client import EmbeddingClient
from cbhcli_pkg.core.rerank_client import RerankClient
from cbhcli_pkg.core.skill_manager import SkillManager

from cbhcli_pkg.commands.parser import SlashCommandParser, SlashCommand
from cbhcli_pkg.commands.agent_cmd import register_agent_commands
from cbhcli_pkg.commands.session_cmd import register_session_commands
from cbhcli_pkg.commands.model_cmd import register_model_commands
from cbhcli_pkg.commands.kb_cmd import register_kb_commands
from cbhcli_pkg.commands.embedding_cmd import register_embedding_commands
from cbhcli_pkg.commands.mcp_cmd import register_mcp_commands
from cbhcli_pkg.commands.skills_cmd import register_skills_commands
from cbhcli_pkg.commands.tools_cmd import register_tools_commands
from cbhcli_pkg.commands.fallback_cmd import register_fallback_commands


class SlashCommandHelper:
    """斜杠命令补全数据计算（不依赖 prompt_toolkit Completer）
    
    返回 [(display, description, full_command), ...] 列表
    """
    
    SUBCOMMANDS = {
        'agent': [
            ('add', '创建新Agent'),
            ('list', '列出所有Agent'),
            ('use', '切换到指定Agent'),
            ('rm', '删除Agent'),
        ],
        'model': [
            ('add', '添加新模型'),
            ('list', '列出所有模型'),
            ('use', '使用指定模型'),
            ('rm', '删除模型'),
            ('info', '查看当前模型信息'),
            ('config', '修改模型参数'),
            ('embedding', '配置嵌入模型'),
            ('rerank', '配置重排序模型'),
        ],
        'model embedding': [
            ('add', '添加嵌入模型'),
            ('info', '查看当前嵌入模型'),
            ('rm', '删除嵌入模型'),
        ],
        'model rerank': [
            ('add', '添加重排序模型'),
            ('info', '查看当前重排序模型'),
            ('rm', '删除重排序模型'),
        ],
        'kb': [
            ('add', '添加文件到知识库'),
            ('list', '列出知识库文件'),
            ('rm', '从知识库删除文件'),
            ('reindex', '重新索引知识库'),
            ('status', '查看知识库状态'),
        ],
        'mcp': [
            ('add', '添加MCP服务器'),
            ('list', '列出所有MCP服务器'),
            ('rm', '移除MCP服务器'),
            ('refresh', '刷新MCP服务器'),
            ('tools', '查看服务器工具列表'),
            ('on', '启用MCP工具'),
            ('off', '禁用MCP工具'),
        ],
        'embedding': [
            ('index', '索引当前Agent工作空间'),
            ('status', '查看索引状态'),
            ('clear', '清除索引'),
            ('reindex', '重新索引'),
        ],
        'skills': [
            ('list', '列出所有已注册技能'),
            ('add', '创建技能'),
            ('use', '选择激活技能'),
            ('off', '取消激活技能'),
            ('rm', '删除技能'),
        ],
        'tools': [
            ('list', '查看所有工具状态'),
            ('on', '开启工具（交互式多选）'),
            ('off', '关闭工具（交互式多选）'),
        ],
        'fallback': [
            ('add', '添加备用模型'),
            ('list', '查看备用模型配置'),
            ('rm', '移除备用模型'),
            ('reorder', '重新排序备用模型'),
            ('clear', '清空备用模型列表'),
        ],
        'fallback add': [
            ('main', '添加主模型备用'),
            ('vision', '添加视觉模型备用'),
        ],
        'fallback rm': [
            ('main', '移除主模型备用'),
            ('vision', '移除视觉模型备用'),
        ],
        'fallback reorder': [
            ('main', '重排主模型备用'),
            ('vision', '重排视觉模型备用'),
        ],
        'fallback clear': [
            ('main', '清空主模型备用'),
            ('vision', '清空视觉模型备用'),
        ],
    }
    
    def __init__(self, command_parser):
        self.command_parser = command_parser
    
    def compute(self, text):
        """计算补全列表
        
        Args:
            text: 当前输入文本
            
        Returns:
            [(display, description, full_command), ...]
        """
        if not text.startswith('/'):
            return []
        
        after_slash = text[1:]
        parts = after_slash.split()
        trailing_space = after_slash.endswith(' ')
        commands = self.command_parser.get_all_commands()
        results = []
        
        # 阶段1：正在输入主命令
        if not parts or (len(parts) == 1 and not trailing_space):
            prefix = after_slash.lower()
            for name in sorted(commands):
                if name.startswith(prefix):
                    cmd = commands[name]
                    results.append((f'/{name}', cmd.description, f'/{name}'))
                    if name == prefix and name in self.SUBCOMMANDS:
                        for sn, sd in self.SUBCOMMANDS[name]:
                            results.append((f'  {sn}', sd, f'/{name} {sn}'))
            return results
        
        main_cmd = parts[0].lower()
        
        # 阶段2：主命令后空格，显示一级子命令
        if len(parts) == 1 and trailing_space:
            if main_cmd in self.SUBCOMMANDS:
                for sn, sd in self.SUBCOMMANDS[main_cmd]:
                    results.append((sn, sd, f'/{main_cmd} {sn}'))
            return results
        
        # 阶段3：正在输入一级子命令
        if len(parts) == 2 and not trailing_space:
            sub_prefix = parts[1].lower()
            if main_cmd in self.SUBCOMMANDS:
                for sn, sd in self.SUBCOMMANDS[main_cmd]:
                    if sn.startswith(sub_prefix):
                        results.append((sn, sd, f'/{main_cmd} {sn}'))
                        two_key = f'{main_cmd} {sn}'
                        if sn == sub_prefix and two_key in self.SUBCOMMANDS:
                            for s3n, s3d in self.SUBCOMMANDS[two_key]:
                                results.append((f'  {s3n}', s3d, f'/{main_cmd} {sn} {s3n}'))
            return results
        
        # 阶段4：一级子命令后空格，显示三级子命令
        if len(parts) == 2 and trailing_space:
            two_key = f'{main_cmd} {parts[1].lower()}'
            if two_key in self.SUBCOMMANDS:
                for sn, sd in self.SUBCOMMANDS[two_key]:
                    results.append((sn, sd, f'/{main_cmd} {parts[1]} {sn}'))
            return results
        
        # 阶段5：正在输入三级子命令
        if len(parts) == 3 and not trailing_space:
            two_key = f'{main_cmd} {parts[1].lower()}'
            if two_key in self.SUBCOMMANDS:
                sub_prefix = parts[2].lower()
                for sn, sd in self.SUBCOMMANDS[two_key]:
                    if sn.startswith(sub_prefix):
                        results.append((sn, sd, f'/{main_cmd} {parts[1]} {sn}'))
            return results
        
        return []


class CBHCLIApp:
    """CBHCLI主应用
    
    负责：
    - 应用初始化和配置
    - Agent管理
    - 用户交互循环
    - 命令路由
    """
    
    def __init__(self):
        """初始化应用"""
        self._init_config()
        self._init_tools()
        self._init_vector_store()
        self._init_commands()
        self._init_ui()
        self._init_agent()
    
    def _init_config(self):
        """初始化配置"""
        self.global_config = GlobalConfig()
        
        workspace_base = Path(self.global_config.get_settings().get(
            "workspace_base",
            str(Path.home() / ".cbhcli" / "agents")
        ))
        self.agent_manager = AgentManager(workspace_base)
        self.token_counter = get_token_counter()
        self.subagent_scheduler = SubAgentScheduler()
    
    def _init_tools(self):
        """初始化工具系统"""
        self.tool_registry = ToolRegistry()
        self.tool_registry.register(TerminalTool())
        self.tool_registry.register(ReadTool())
        self.tool_registry.register(WriteTool())
        self.tool_registry.register(EditTool())
        self.tool_registry.register(PythonTool("default"))
        self.tool_registry.register(SkillsCreateTool(self))
        self.tool_registry.register(DelegateTaskTool(self))
        self.tool_registry.register(GrepTool())
        self.tool_registry.register(GlobTool())
        self.tool_registry.register(AskUserQuestionTool())
        self.tool_registry.register(ImageTool(self))
        self.tool_registry.register(ProcessTool())
        self.tool_registry.register(KillProcessTool())
        self.todo_tool = TodoTool()
        self.tool_registry.register(self.todo_tool)

        # cbhpacks 数据科学工具（默认关闭，用户可通过 /tools on 开启）
        self.tool_registry.register(BinsModelTool())
        self.tool_registry.register(BinaryModelTool())
        self.tool_registry.register(UnsModelTool())
        self.tool_registry.register(LinearModelTool())
        self.tool_registry.register(ColsSelectTool())
        self.tool_registry.register(ColsSelectJsTool())
        self.tool_registry.register(ColsEncodeTool())
        self.tool_registry.register(ColsOperateTool())
        self.tool_registry.register(DescDfTool())
        self.tool_registry.register(DescColTool())
        self.tool_registry.register(ConSqlTool())
        self.tool_registry.register(ConLinuxTool())
        self.tool_registry.register(GetRandomDataTool())
        
        # 工具执行器（延迟初始化vector_store后添加memory_search）
        self.tool_executor = ToolExecutor(self.tool_registry)
    
    def _init_vector_store(self):
        """初始化向量存储（可选）"""
        self.vector_store: Optional[VectorStore] = None
        self.memory_indexer: Optional[MemoryIndexer] = None
        self.embedding_client = None
        self.rerank_client = None
        
        # 初始化嵌入模型客户端
        embedding_config = self.global_config.get_embedding_model()
        if embedding_config:
            try:
                self.embedding_client = EmbeddingClient(embedding_config)
            except Exception as e:
                print(f"⚠️  嵌入模型初始化失败: {e}")
        
        # 初始化重排序客户端
        rerank_config = self.global_config.get_rerank_model()
        if rerank_config:
            try:
                self.rerank_client = RerankClient(rerank_config)
            except Exception as e:
                print(f"⚠️  重排序模型初始化失败: {e}")
        
        # 只有在配置了嵌入模型时才初始化向量存储
        if self.embedding_client:
            try:
                vector_dir = Path.home() / ".cbhcli" / "vectors"
                self.vector_store = VectorStore(
                    vector_dir, 
                    embedding_client=self.embedding_client
                )
                self.memory_indexer = MemoryIndexer(self.vector_store)
                
                # 添加 memory_search 工具
                memory_search = MemorySearchTool(
                    vector_store=self.vector_store,
                    agent_manager=self.agent_manager,
                    app=self  # 传入 app 引用，用于自动获取当前 Agent 名称
                )
                self.tool_registry.register(memory_search)
                
                # 添加 knowledge_base 工具
                kb_tool = KnowledgeBaseTool(
                    vector_store=self.vector_store,
                    agent_manager=self.agent_manager,
                    rerank_client=self.rerank_client,
                    app=self  # 传入 app 引用
                )
                self.tool_registry.register(kb_tool)
            except Exception as e:
                print(f"⚠️  向量存储初始化失败: {e}")
        else:
            print("💡 提示: 使用 /model embedding add 配置嵌入模型以启用向量搜索功能")
    
    def _init_commands(self):
        """初始化命令系统"""
        self.command_parser = SlashCommandParser()
        
        register_agent_commands(self.command_parser, self)
        register_session_commands(self.command_parser, self)
        register_model_commands(self.command_parser, self)
        register_kb_commands(self.command_parser, self)
        register_embedding_commands(self.command_parser, self)
        register_mcp_commands(self.command_parser, self)
        register_skills_commands(self.command_parser, self)
        register_tools_commands(self.command_parser, self)
        register_fallback_commands(self.command_parser, self)
        
        # 注册help命令
        def help_handler(args):
            if args.strip():
                cmd = self.command_parser.get_command(args.strip())
                if cmd:
                    return f"/{cmd.name}\n{cmd.description}\n用法: /{cmd.name} {cmd.usage}"
                else:
                    return f"未知命令: {args}"
            else:
                return self.command_parser.get_help_text()
        
        self.command_parser.register(SlashCommand(
            name="help",
            description="显示帮助信息",
            usage="[command]",
            handler=help_handler
        ))
    
    def _init_ui(self):
        """初始化UI组件"""
        self.tool_verbose = False
        
        # 斜杠命令补全助手
        self._cmd_helper = SlashCommandHelper(self.command_parser)
        
        # 聊天输入框组件（基于 prompt_toolkit 原生补全系统）
        self._chat_input = ChatInputBox(self._cmd_helper, self)
    
    def _init_agent(self):
        """初始化Agent"""
        # 确保存在main agent（不触发索引）
        if not self.agent_manager.load_agent("main"):
            self.agent_manager.create_agent("main", "主默认Agent")
            self.global_config.set_active_agent("main")
        
        # 加载当前Agent
        self.current_agent_name: Optional[str] = None
        self.current_agent_config: Optional[AgentConfig] = None
        self.current_persona: Optional[AgentPersona] = None
        
        self.session: Optional[Session] = None
        self.context_window: Optional[ContextWindow] = None
        self.llm_client: Optional[LLMClient] = None
        self.context_compressor: Optional[ContextCompressor] = None
        self.session_history: Optional[SessionHistoryManager] = None
        self.mcp_manager: Optional[MCPManager] = None
        self.skill_manager: Optional[SkillManager] = None
        self._agent_indexed: bool = False  # 标记是否已索引
        
        # 尝试加载上次活动的Agent
        active_agent = self.global_config.get_active_agent()
        if active_agent and self._load_agent(active_agent):
            pass
        else:
            self._load_agent("main")
    
    def _load_agent(self, agent_name: str, do_index: bool = False) -> bool:
        """加载指定Agent
        
        Args:
            agent_name: Agent名称
            do_index: 是否索引工作空间（默认不索引，由 /embedding 命令手动触发）
            
        Returns:
            是否加载成功
        """
        config = self.agent_manager.load_agent(agent_name)
        if not config:
            return False
        
        self.current_agent_config = config
        self.current_agent_name = agent_name
        self.current_persona = self.agent_manager.load_agent_persona(agent_name)
        
        # 初始化LLM客户端
        model_name = config.primary_model or self.global_config.get_last_selected_model()
        if model_name:
            model_config = self.global_config.get_model(model_name)
            if model_config:
                self.llm_client = LLMClient(model_config)
                self.token_counter = get_token_counter(model_config.get("model"))
                self.context_compressor = ContextCompressor(
                    self.llm_client, self.token_counter
                )
        
        # 初始化会话历史管理器
        self.session_history = SessionHistoryManager(config.workspace_path)
        
        # 初始化 MCP 管理器（每个 Agent 独立）
        self.mcp_manager = MCPManager(agent_name, config.workspace_path, self.tool_registry)
        
        # 初始化技能管理器（每个 Agent 独立）
        self.skill_manager = SkillManager(config.workspace_path)

        # 应用 Agent 工具开关设置
        self.tool_registry.set_disabled_tools(config.disabled_tools or [])
        
        # 索引 Agent 工作空间（如果向量数据库可用，且尚未索引）
        if do_index and not self._agent_indexed and self.memory_indexer and config.workspace_path.exists():
            try:
                segments = self.memory_indexer.index_agent_workspace(
                    agent_name, config.workspace_path
                )
                if segments > 0:
                    print(f"📚 已索引 {segments} 个段落到向量数据库")
                self._agent_indexed = True  # 标记已索引，防止重复
            except Exception as e:
                print(f"⚠️  索引工作空间失败: {e}")
        
        self._reset_session()
        return True

    def switch_model(self, model_config: dict):
        """原地切换模型，保留当前会话全部内容

        供 /model use 调用：仅替换 LLM 客户端及关联组件（token计数/压缩器/
        上下文窗口限制），会话消息原样保留，系统提示原地更新
        （模型名称、视觉能力描述可能随模型变化）。

        Args:
            model_config: 新模型配置字典
        """
        self.llm_client = LLMClient(model_config)
        self.token_counter = get_token_counter(model_config.get("model"))
        self.context_compressor = ContextCompressor(
            self.llm_client, self.token_counter
        )

        # 更新上下文窗口的模型限制（新模型的 context_limit 可能不同）
        if self.context_window:
            self.context_window.model_limit = self.llm_client.context_limit

        # 原地更新系统提示（会话消息不受影响）
        self._update_system_prompt()
    
    def _reset_session(self, save_current: bool = True):
        """重置会话
        
        Args:
            save_current: 是否保存当前会话（默认保存）
        """
        if not self.current_agent_config:
            return
        
        # 保存当前会话到 history 文件夹
        if save_current and self.session and len(self.session.messages) > 1:
            try:
                ctx_msgs = self.session.get_context_messages()
                self.session_history.save_session(ctx_msgs, self.session.id)
            except Exception:
                pass
        
        # 重置 Python 会话（清空变量记忆）
        remove_python_session("default")
        
        self.session = Session(agent_name=self.current_agent_name)
        
        # 获取模型名称
        model_name = ""
        if self.llm_client and hasattr(self.llm_client, 'model_name'):
            model_name = self.llm_client.model_name
        elif self.current_agent_config.primary_model:
            model_name = self.current_agent_config.primary_model
        
        # 读取 memory.md 内容，始终包含在系统提示中
        memory_content = self._load_memory_md()
        
        # 获取已激活技能的提示内容
        active_skills_prompt = ""
        if self.skill_manager:
            active_skills_prompt = self.skill_manager.build_skills_prompt()
        
        supports_vision = self.llm_client.supports_vision if self.llm_client else False
        system_prompt = self.current_persona.build_system_prompt(
            agent_name=self.current_agent_name or "",
            model_name=model_name,
            memory_content=memory_content,
            active_skills_prompt=active_skills_prompt,
            cwd=os.getcwd(),
            supports_vision=supports_vision
        )
        system_token_count = self.token_counter.count_tokens(system_prompt)
        self.session.add_message("system", system_prompt, token_count=system_token_count)
        
        # 计算 OpenAI tools schema 的 token 开销
        openai_tools = self.tool_registry.get_openai_tools()
        tools_schema_tokens = 0
        if openai_tools:
            tools_schema_tokens = self.token_counter.count_tokens(
                json.dumps(openai_tools, ensure_ascii=False)
            )
        
        # 初始化上下文窗口
        model_limit = DEFAULT_CONTEXT_LIMIT
        if self.llm_client:
            model_limit = self.llm_client.context_limit
        
        self.context_window = ContextWindow(
            model_limit=model_limit,
            compression_ratio=self.current_agent_config.context_limit_ratio or DEFAULT_COMPRESSION_RATIO,
            tools_schema_tokens=tools_schema_tokens
        )
    
    def _update_system_prompt(self):
        """原地更新当前会话的系统提示词（不重置会话，保留对话历史）
        
        用于 MCP 工具或技能变化后，刷新系统提示中的工具描述和技能内容。
        """
        if not self.session or not self.current_persona or not self.current_agent_config:
            return
        
        # 获取模型名称
        model_name = ""
        if self.llm_client and hasattr(self.llm_client, 'model_name'):
            model_name = self.llm_client.model_name
        elif self.current_agent_config.primary_model:
            model_name = self.current_agent_config.primary_model
        
        memory_content = self._load_memory_md()
        
        active_skills_prompt = ""
        if self.skill_manager:
            active_skills_prompt = self.skill_manager.build_skills_prompt()
        
        supports_vision = self.llm_client.supports_vision if self.llm_client else False
        system_prompt = self.current_persona.build_system_prompt(
            agent_name=self.current_agent_name or "",
            model_name=model_name,
            memory_content=memory_content,
            active_skills_prompt=active_skills_prompt,
            cwd=os.getcwd(),
            supports_vision=supports_vision
        )
        
        # 原地替换 system 消息
        if self.session.messages and self.session.messages[0].role == "system":
            self.session.messages[0].content = system_prompt
            self.session.messages[0].token_count = self.token_counter.count_tokens(system_prompt)
        else:
            # 没有 system 消息则插入
            msg = Message(
                role="system",
                content=system_prompt,
                token_count=self.token_counter.count_tokens(system_prompt)
            )
            self.session.messages.insert(0, msg)
        
        # 更新 tools schema tokens（MCP/skills 变化可能改变工具列表）
        if self.context_window:
            openai_tools = self.tool_registry.get_openai_tools()
            if openai_tools:
                self.context_window.tools_schema_tokens = self.token_counter.count_tokens(
                    json.dumps(openai_tools, ensure_ascii=False)
                )
            else:
                self.context_window.tools_schema_tokens = 0
    
    def _load_memory_md(self) -> str:
        """读取 memory.md 文件内容
        
        Returns:
            memory.md 的内容，如果文件不存在返回空字符串
        """
        if not self.current_agent_config:
            return ""
        
        memory_file = self.current_agent_config.workspace_path / "memory.md"
        if memory_file.exists():
            try:
                content = memory_file.read_text(encoding='utf-8')
                # 移除模板说明部分，只保留实际内容
                lines = content.split('\n')
                # 跳过 "---" 之前的行
                in_content = False
                content_lines = []
                for line in lines:
                    if line.strip() == '---':
                        in_content = True
                        continue
                    if in_content:
                        content_lines.append(line)
                return '\n'.join(content_lines).strip()
            except Exception:
                pass
        return ""
    
    def _compress_context(self) -> bool:
        """压缩上下文"""
        if not self.context_compressor or not self.session:
            return False
        target_tokens = self.context_window.trigger_threshold()
        return self.context_compressor.compress(self.session, target_tokens)
    
    def _check_and_compress_context(self):
        """检查并自动压缩上下文"""
        if not self.context_window or not self.session:
            return
        
        total_tokens = self.session.get_total_tokens(self.token_counter)
        self.context_window.update(total_tokens)
        
        if (self.current_agent_config and
            self.current_agent_config.auto_compress and
            self.context_window.needs_compression()):
            print(f"\n{C_DIM}上下文接近上限 ({self.context_window.get_status_text()})")
            print("正在自动压缩...")
            if self._compress_context():
                print("上下文已压缩")
            else:
                print("压缩失败")
    
    def run(self):
        """主运行循环"""
        self._show_welcome()
        
        while True:
            try:
                user_input = self._get_input()
                
                if not user_input:
                    continue
                
                if user_input.lower() == 'quit':
                    # 退出前保存当前会话
                    if self.session and self.session_history and len(self.session.messages) > 1:
                        try:
                            ctx_msgs = self.session.get_context_messages()
                            self.session_history.save_session(ctx_msgs, self.session.id)
                        except Exception:
                            pass
                    print("\n再见!")
                    break
                
                self._print_user_input(user_input)
                
                # 处理命令
                is_command, output = self.command_parser.execute(user_input)
                if is_command:
                    print(f"\n{output}\n")
                    continue
                # 命令识别但执行失败（如异常），或未知斜杠命令，也需要输出错误而非 fallthrough 到 AI
                if output:
                    print(f"\n{output}\n")
                    continue
                
                # 处理AI请求
                if self.llm_client and self.session:
                    self._handle_ai_request(user_input)
                else:
                    print(f"\n{C_DIM}当前Agent未配置模型。请使用 /model 命令配置模型。{C_RESET}")
                
            except KeyboardInterrupt:
                print(f"\n\n{C_DIM}操作被中断。输入 'quit' 退出。{C_RESET}")
                continue
            except Exception as e:
                print(f"\n错误: {str(e)}{C_RESET}")
                continue
    
    def _handle_ai_request(self, user_input: str):
        """处理AI请求 - 委托给AIHandler"""
        # 检查上下文压缩
        self._check_and_compress_context()
        
        # 创建AI处理器
        handler = AIHandler(
            self.llm_client,
            self.session,
            self.tool_executor,
            self.token_counter
        )
        
        # 注入子Agent调度器
        handler.subagent_scheduler = self.subagent_scheduler
        handler.agent_name = self.current_agent_name or "main"
        
        # 注入备用主模型列表
        from cbhcli_pkg.config.global_config import GlobalConfig
        gc = GlobalConfig()
        handler.fallback_models = gc.get_fallback_models()
        
        # 注入上下文压缩相关组件（用于 ReAct 循环内自动压缩）
        handler.context_compressor = self.context_compressor
        handler.context_window = self.context_window
        handler.auto_compress = (
            self.current_agent_config.auto_compress
            if self.current_agent_config else True
        )
        
        # 设置记忆更新回调
        handler.on_memory_update(self._update_memory)
        
        # 处理请求
        handler.process_request(user_input)
    
    def _update_memory(self, user_input: str, ai_response: str):
        """更新记忆回调 - 仅用于保存会话历史
        
        memory.md 只保存用户明确要求记录的长期记忆。
        对话历史自动保存到 history 文件夹。
        """
        pass  # 会话历史在 _reset_session 时自动保存
    
    def _show_welcome(self):
        """显示欢迎信息"""
        from cbhcli_pkg import __version__
        tw = self._get_terminal_width()
        print(f"\n{C_SEP}{'═' * tw}{C_RESET}")
        print(f"  CBHCLI v{__version__} - AI命令行助手")
        print(f"{C_DIM}  输入 'quit' 退出 | /help 查看帮助{C_RESET}")
        if self.current_agent_name:
            print(f"{C_DIM}  当前Agent: {self.current_agent_name}{C_RESET}")
        print(f"{C_SEP}{'═' * tw}{C_RESET}\n")
    
    @staticmethod
    def _get_terminal_width() -> int:
        """获取终端宽度"""
        try:
            return os.get_terminal_size().columns
        except (ValueError, OSError):
            return 80
    
    def _get_input(self) -> str:
        """获取用户输入（带输入框）"""
        return self._chat_input.prompt()
    
    @staticmethod
    def _display_width(text: str) -> int:
        """计算文本显示宽度（中文占2，英文占1）"""
        try:
            from wcwidth import wcswidth
            w = wcswidth(text)
            return w if w >= 0 else len(text)
        except ImportError:
            return len(text)
    
    def _print_user_input(self, user_input: str):
        """打印用户输入回显（提交后高亮显示）"""
        self._chat_input.print_user_echo(user_input)
