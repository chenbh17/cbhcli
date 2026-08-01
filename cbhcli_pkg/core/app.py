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
from cbhcli_pkg.core.permissions import PermissionEngine, MODE_META
from cbhcli_pkg.core.hooks import HookManager
from cbhcli_pkg.core.checkpoint import CheckpointManager
from cbhcli_pkg.core.tracer import Tracer
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
from cbhcli_pkg.commands.harness_cmd import register_harness_commands
from cbhcli_pkg.commands.chain_cmd import register_chain_commands


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
        'mode': [
            ('readonly', '只读模式：AI 只能查看不能修改'),
            ('standard', '标准模式：危险操作逐个确认（默认）'),
            ('auto', '自动模式：工作区内写操作自动放行'),
            ('yolo', '最高权限：全部直接执行零确认'),
            ('list', '查看当前模式与所有模式说明'),
        ],
        'permissions': [
            ('list', '查看权限规则'),
            ('add', '添加规则: /permissions add <allow|ask|deny> <规则>'),
            ('rm', '删除规则: /permissions rm <allow|ask|deny> <规则>'),
        ],
        'hooks': [
            ('list', '查看已配置钩子'),
            ('reload', '重新加载 hooks.json'),
            ('test', '测试触发钩子: /hooks test <事件名>'),
        ],
        'undo': [
            ('list', '查看可回滚的文件备份'),
        ],
        'chain': [
            ('list', '列出所有链条'),
            ('add', '创建新链条'),
            ('rm', '删除链条'),
            ('use', '激活链条'),
            ('off', '取消链条绑定'),
            ('show', '查看链条详情'),
            ('config', '编辑链条配置'),
            ('rename', '重命名链条'),
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

        # 权限规则引擎（Harness 治理层，全局单例，Shift+Tab 热切换模式）
        self.permission_engine = PermissionEngine()
        self.tool_executor.permission_engine = self.permission_engine
    
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
        register_harness_commands(self.command_parser, self)
        register_chain_commands(self.command_parser, self)
        
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
        
        # Agent 链条状态
        self._active_chain = None          # 当前激活的 AgentChain
        self._chain_active_path: list = None  # 当前执行路径 ["main", "cbhcli", ...]
        self._chain_manager = None         # ChainManager 延迟初始化
        
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
                    self.llm_client, self.token_counter,
                    workspace_path=config.workspace_path
                )
        
        # 初始化会话历史管理器
        self.session_history = SessionHistoryManager(config.workspace_path)
        
        # 初始化 MCP 管理器（每个 Agent 独立）
        self.mcp_manager = MCPManager(agent_name, config.workspace_path, self.tool_registry)
        
        # 初始化技能管理器（每个 Agent 独立）
        self.skill_manager = SkillManager(config.workspace_path)

        # Harness 组件（每个 Agent 独立，挂到工具执行器）
        self.hook_manager = HookManager(config.workspace_path, agent_name)
        self.checkpoint_manager = CheckpointManager(config.workspace_path)
        self.tool_executor.hook_manager = self.hook_manager
        self.tool_executor.checkpoint_manager = self.checkpoint_manager

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

        # Agent 链条：恢复持久化的激活状态
        if not getattr(self, '_active_chain', None):
            saved_chain_name = self.global_config.get_active_chain(agent_name)
            if saved_chain_name:
                from cbhcli_pkg.commands.chain_cmd import _get_chain_manager
                cm = _get_chain_manager(self)
                saved_chain = cm.get_chain(saved_chain_name)
                if saved_chain:
                    # 校验 Agent 存在性
                    missing = saved_chain.validate(self.agent_manager)
                    if not missing and saved_chain.get_root_agent() == agent_name:
                        from cbhcli_pkg.commands.chain_cmd import _activate_chain
                        _activate_chain(self, saved_chain)
                        print(f"{C_DIM}🔗 已恢复链条: {saved_chain_name}{C_RESET}")
        else:
            # 已有激活链条：检查是否匹配当前 Agent
            chain = self._active_chain
            root = chain.get_root_agent()
            if agent_name != root:
                # 切换的 Agent 不是当前链条的元 Agent，取消链条绑定
                from cbhcli_pkg.commands.chain_cmd import _deactivate_chain
                _deactivate_chain(self)
                print(f"{C_DIM}💡 切换 Agent 导致链条绑定已取消{C_RESET}")
            else:
                # 重新注入链条信息
                from cbhcli_pkg.commands.chain_cmd import _inject_chain_prompt, _register_call_agent_tool
                _inject_chain_prompt(self, chain)
                _register_call_agent_tool(self, chain)

        # SessionStart 钩子（stdout 打印给用户）
        if self.hook_manager and self.hook_manager.has_hooks("SessionStart"):
            decision = self.hook_manager.run_simple(
                "SessionStart",
                session_id=self.session.id if self.session else "")
            for line in decision.outputs:
                print(f"{C_DIM}[hook:SessionStart] {line}{C_RESET}")
            for warn in decision.warnings:
                print(f"{C_DIM}⚠️ 钩子: {warn}{C_RESET}")
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
            self.llm_client, self.token_counter,
            workspace_path=getattr(self.current_agent_config, "workspace_path", None)
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

        # 可观测性 tracer（每会话一个 JSONL 文件，挂到工具执行器）
        self.tracer = Tracer(self.current_agent_config.workspace_path,
                             self.session.id)
        self.tool_executor.tracer = self.tracer
        self.tool_executor.session_id = self.session.id

        # 权限模式回落默认 + 确认状态随新会话重置
        if self.permission_engine:
            self.permission_engine.reset_to_default()
        self.tool_executor.no_more_confirmations = False

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
        # 注入当前权限模式说明（readonly/auto/yolo 时告知模型行为边界）
        system_prompt += self._permission_mode_note()
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
        # 注入当前权限模式说明（与 _reset_session 保持一致）
        system_prompt += self._permission_mode_note()

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
    
    # ==================================================================
    #  权限模式管理（Harness 治理层）
    # ==================================================================

    def cycle_permission_mode(self) -> str:
        """Shift+Tab 循环切换权限模式（由输入框快捷键调用）

        Returns:
            切换后的新模式名
        """
        old_mode = self.permission_engine.mode
        new_mode = self.permission_engine.cycle_mode()
        if getattr(self, "tracer", None):
            self.tracer.log_mode_change(old_mode, new_mode)
        # 模式说明注入系统提示（原地更新，不影响会话）
        self._update_system_prompt()
        return new_mode

    def set_permission_mode(self, mode: str) -> bool:
        """/mode 命令设置权限模式"""
        if not self.permission_engine or mode not in MODE_META:
            return False
        old_mode = self.permission_engine.mode
        self.permission_engine.set_mode(mode)
        if getattr(self, "tracer", None):
            self.tracer.log_mode_change(old_mode, mode)
        self._update_system_prompt()
        return True

    def _permission_mode_note(self) -> str:
        """根据当前权限模式生成注入系统提示的说明文本"""
        if not getattr(self, "permission_engine", None):
            return ""
        from cbhcli_pkg.core.permissions import build_mode_note
        return build_mode_note(self.permission_engine.mode)

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
    
    def _compress_context(self, instructions: str = "") -> bool:
        """压缩上下文

        Args:
            instructions: 可选的压缩指令（保留/丢弃重点），透传给摘要模型
        """
        if not self.context_compressor or not self.session:
            return False
        before = self.session.get_total_tokens(self.token_counter)
        target_tokens = self.context_window.compression_target()
        success = self.context_compressor.compress(
            self.session, target_tokens, instructions=instructions or None)
        if getattr(self, "tracer", None):
            after = self.session.get_total_tokens(self.token_counter)
            self.tracer.log_compress(success, before, after)
        return success
    
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

        # UserPromptSubmit 钩子：stdout 追加为用户上下文（如 git 分支状态）
        if getattr(self, "hook_manager", None) and \
                self.hook_manager.has_hooks("UserPromptSubmit"):
            decision = self.hook_manager.run_simple(
                "UserPromptSubmit",
                extra_args={"prompt": user_input},
                session_id=self.session.id if self.session else "")
            extra = decision.merged_output()
            if extra:
                user_input = f"{user_input}\n\n[钩子补充上下文]\n{extra}"
            for warn in decision.warnings:
                print(f"{C_DIM}⚠️ 钩子: {warn}{C_RESET}")

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

        # Stop 钩子：AI 回复完成后触发（通知/自动保存等）
        if getattr(self, "hook_manager", None) and \
                self.hook_manager.has_hooks("Stop"):
            decision = self.hook_manager.run_simple(
                "Stop", session_id=self.session.id if self.session else "")
            for line in decision.outputs:
                print(f"{C_DIM}[hook:Stop] {line}{C_RESET}")
            for warn in decision.warnings:
                print(f"{C_DIM}⚠️ 钩子: {warn}{C_RESET}")
    
    def _update_memory(self, user_input: str, ai_response: str):
        """更新记忆回调 - 仅用于保存会话历史
        
        memory.md 只保存用户明确要求记录的长期记忆。
        对话历史自动保存到 history 文件夹。
        """
        pass  # 会话历史在 _reset_session 时自动保存
    
    # 权限模式在欢迎面板中的圆点颜色（ANSI）
    _MODE_ANSI = {
        "readonly": "\033[94m",   # 亮蓝
        "standard": "\033[92m",   # 亮绿
        "auto": "\033[93m",       # 亮黄
        "yolo": "\033[91m",       # 亮红
    }

    def _show_welcome(self):
        """显示欢迎信息（ASCII 艺术字 + 信息面板）

        全部使用宽度安全的字符（█/╗/● 等宽1字符），动态内容用
        text_width.display_width（字素簇感知）计算补齐，杜绝 emoji
        宽度歧义导致的边框错位。
        """
        from cbhcli_pkg import __version__
        from cbhcli_pkg.core.text_width import display_width as _dw

        # CBHCLI 艺术字（46 列宽，纯宽1字符）
        art = [
            " ██████╗██████╗ ██╗  ██╗ ██████╗██╗     ██╗",
            "██╔════╝██╔══██╗██║  ██║██╔════╝██║     ██║",
            "██║     ██████╔╝███████║██║     ██║     ██║",
            "██║     ██╔══██╗██╔══██║██║     ██║     ██║",
            "╚██████╗██████╔╝██║  ██║╚██████╗███████╗██║",
            " ╚═════╝╚═════╝ ╚═╝  ╚═╝ ╚═════╝╚══════╝╚═╝",
        ]
        C_ART = "\033[96m"      # 亮青
        C_TXT = "\033[97m"      # 亮白
        C_VAL = "\033[36m"      # 青

        agent = self.current_agent_name or "main"
        model_name = ""
        if self.llm_client and hasattr(self.llm_client, 'model_name'):
            model_name = self.llm_client.model_name
        elif self.current_agent_config and self.current_agent_config.primary_model:
            model_name = self.current_agent_config.primary_model
        mode = self.permission_engine.mode \
            if getattr(self, "permission_engine", None) else "standard"
        mode_meta = MODE_META[mode]
        mode_ansi = self._MODE_ANSI.get(mode, "")

        # 信息行（分段着色；纯文本部分用于宽度计算）
        info1 = [(f"v{__version__} · AI 驱动的终端助手", C_DIM)]
        info2 = [("Agent ", C_DIM), (agent, C_VAL),
                 ("  │  模型 ", C_DIM), (model_name or "未配置", C_VAL),
                 ("  │  权限 ", C_DIM), ("●", mode_ansi),
                 (f" {mode_meta['label']}", C_TXT)]
        info3 = [("quit 退出 · /help 帮助 · Shift+Tab 切换权限模式", C_DIM)]

        # 内容宽度 = 艺术字、信息行中最宽者
        def _segs_width(segs):
            return _dw("".join(t for t, _ in segs))

        content_w = max(
            max(_dw(line) for line in art),
            _segs_width(info1), _segs_width(info2), _segs_width(info3),
        )
        content_w = min(content_w, self._get_terminal_width() - 6)

        def _row(segments, color: str = "") -> str:
            body_plain = "".join(t for t, _ in segments)
            pad = max(0, content_w - _dw(body_plain))
            body = "".join(
                f"{c}{t}{C_RESET}" if c else t for t, c in segments)
            return f"{C_SEP}│{C_RESET} {body}{' ' * pad} {C_SEP}│{C_RESET}"

        border = "─" * (content_w + 2)
        print(f"\n{C_SEP}╭{border}╮{C_RESET}")
        for line in art:
            print(_row([(line, C_ART)]))
        print(_row([("", "")]))  # 空行
        print(_row(info1))
        print(_row(info2))
        print(_row(info3))
        print(f"{C_SEP}╰{border}╯{C_RESET}\n")
    
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
