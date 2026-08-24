"""CBHCLI Web Server - FastAPI backend (v2 重构版)

与 CLI 端逻辑完全对齐：
- WebChatSession 统一封装会话状态（工具注册表/MCP/技能/上下文压缩组件）
- ReAct 循环：Function Calling + 自我反思重试 + 循环内自动压缩 + 备用模型切换
- 工具确认 / ask_user 通过 SSE 事件 + /api/chat/respond 应答队列实现
- 管理 API：模型/备用模型/Agent/技能/MCP/工具/知识库/向量索引/历史会话/设置
"""
import json
import asyncio
import threading
import os
import uuid
import base64
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Request, UploadFile, File, Form
from fastapi import WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.responses import StreamingResponse, JSONResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from cbhcli_pkg.config.global_config import GlobalConfig, CBHCLI_DIR
from cbhcli_pkg.core.agent import AgentManager, AgentConfig
from cbhcli_pkg.core.session_history import SessionHistoryManager
from cbhcli_pkg.core.model import LLMClient
from cbhcli_pkg.core.session import Session, ContextWindow
from cbhcli_pkg.tools.registry import ToolRegistry, ToolResult
from cbhcli_pkg.tools.terminal import TerminalTool
from cbhcli_pkg.tools.file_read import ReadTool
from cbhcli_pkg.tools.file_write import WriteTool
from cbhcli_pkg.tools.file_edit import EditTool
from cbhcli_pkg.tools.grep import GrepTool
from cbhcli_pkg.tools.glob_tool import GlobTool
from cbhcli_pkg.tools.ask_user import AskUserQuestionTool
from cbhcli_pkg.tools.todo import TodoTool
from cbhcli_pkg.tools.memory_search import MemorySearchTool
from cbhcli_pkg.tools.knowledge_base import KnowledgeBaseTool
from cbhcli_pkg.tools.delegate_task import DelegateTaskTool
from cbhcli_pkg.tools.python_tool import PythonTool, remove_python_session
from cbhcli_pkg.tools.skills_create import SkillsCreateTool
from cbhcli_pkg.tools.image import ImageTool
from cbhcli_pkg.tools.process import ProcessTool
from cbhcli_pkg.tools.kill_process import KillProcessTool
from cbhcli_pkg.core.mcp_manager import MCPManager
from cbhcli_pkg.core.skill_manager import SkillManager
from cbhcli_pkg.core.constants import (
    MAX_TOOL_ROUNDS, MAX_TOOL_OUTPUT_LENGTH, API_TEMPERATURE,
    MAX_REFLECTION_RETRIES,
)
from cbhcli_pkg.core.embedding_client import EmbeddingClient
from cbhcli_pkg.core.rerank_client import RerankClient
from cbhcli_pkg.core.subagent import SubAgentScheduler
from cbhcli_pkg.core.tool_executor import ToolExecutor
from cbhcli_pkg.core.ai_handler import repair_tool_messages
from cbhcli_pkg.core.permissions import (
    PermissionEngine, MODE_META, MODES, build_mode_note,
    ALLOW as PERM_ALLOW, ASK as PERM_ASK, DENY as PERM_DENY,
    WARN as PERM_WARN,
)
from cbhcli_pkg.core.hooks import HookManager
from cbhcli_pkg.core.checkpoint import CheckpointManager
from cbhcli_pkg.core.tracer import Tracer
from cbhcli_pkg.core.loop_detector import ToolCallTracker, TextLoopDetector
from cbhcli_pkg.vector.store import VectorStore
from cbhcli_pkg.vector.indexer import MemoryIndexer
from cbhcli_pkg.context.token_counter import get_token_counter
from cbhcli_pkg.context.compressor import ContextCompressor, SUMMARY_MARKER


def _fix_unicode_escapes(obj):
    """修复 LLM 返回的双斜杠 Unicode 转义序列（与 ai_handler.py 中相同）

    LLM 有时会在 JSON 字符串中返回 \\u2192（双斜杠）而不是 \u2192（单斜杠），
    导致 json.loads 解析后保留字面字符串 '\\u2192'（6个字符）而非实际箭头 '→'。
    """
    if isinstance(obj, dict):
        return {k: _fix_unicode_escapes(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [_fix_unicode_escapes(item) for item in obj]
    elif isinstance(obj, str):
        import re
        pattern = r'\\u([0-9a-fA-F]{4})'
        def replace_match(match):
            try:
                return chr(int(match.group(1), 16))
            except (ValueError, OverflowError):
                return match.group(0)
        return re.sub(pattern, replace_match, obj)
    else:
        return obj


# ===================================================================
#  FastAPI App
# ===================================================================

app = FastAPI(title="CBHCLI Web", version="5.3.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ===================================================================
#  Global State
# ===================================================================

_global_config: Optional[GlobalConfig] = None
_agent_manager: Optional[AgentManager] = None
_vector_store: Optional[VectorStore] = None
_memory_indexer: Optional[MemoryIndexer] = None
_embedding_client = None
_rerank_client = None
_vector_init_attempted = False
_permission_engine: Optional[PermissionEngine] = None


def get_permission_engine() -> PermissionEngine:
    """权限规则引擎（Harness 治理层，全局单例，CLI/Web 共享配置文件）"""
    global _permission_engine
    if _permission_engine is None:
        _permission_engine = PermissionEngine()
    return _permission_engine

# 活跃聊天会话: session_key(agent:model) -> WebChatSession
_chat_sessions: dict[str, 'WebChatSession'] = {}
# 全部存活会话注册表（v5.2.9）: session.id -> WebChatSession
# 会话切换/新建不再中断旧会话，后台运行中的会话全部驻留于此，
# 供侧边栏展示运行状态、任意浏览器按 id 订阅实时事件。
_sessions_by_id: dict[str, 'WebChatSession'] = {}
# WebSocket 连接集合（v5.2.9 实时广播）
_ws_clients: set = set()
# 单次运行事件日志上限（超出裁剪头部，迟到订阅者触发 resync 兜底）
_MAX_RUN_EVENTS = 6000
# 存活会话上限（超出驱逐最旧的空闲会话；数据每轮已自动落盘，安全）
_MAX_LIVE_SESSIONS = 40
# MCP 管理器缓存（管理 API 专用，带独立注册表）: agent_name -> MCPManager
_mcp_managers: dict[str, MCPManager] = {}

# ---- 工作空间（v5.2.8）----
# 服务器启动目录（工作空间浏览的根目录，会话按工作空间分组）
_SERVER_ROOT = Path(os.getcwd()).resolve()
# 当前打开的工作空间目录（打开工作空间时 os.chdir 切换，工具/Agent 随之生效）
_current_workspace: str = str(_SERVER_ROOT)


# 已打开工作空间的持久化记录（打开过即常驻侧边栏，不因切换/重启消失）
_WS_LIST_FILE = CBHCLI_DIR / "web_workspaces.json"


def _load_opened_workspaces() -> list:
    try:
        with open(_WS_LIST_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return [p for p in data if isinstance(p, str)]
    except Exception:
        return []


def _record_opened_workspace(path: str) -> None:
    """记录最近打开的工作空间（最新在前，最多 50 条）。"""
    try:
        lst = [p for p in _load_opened_workspaces() if p != path]
        lst.insert(0, path)
        with open(_WS_LIST_FILE, "w", encoding="utf-8") as f:
            json.dump(lst[:50], f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def _set_current_workspace(path: str) -> None:
    """切换当前工作空间：chdir 服务器进程 + 记录状态 + 刷新活跃会话系统提示。

    v5.2.8 修正：只重建系统提示（cwd 是进程级全局），**不改写各活跃会话
    的 workspace 归属标签**--否则切换工作空间会把别的文件夹下的会话
    "带入"新文件夹（下次保存时归档到错误分组）。
    v5.2.9 修正：只重建**属于新工作空间**的会话的系统提示。会话可后台
    运行后，其他工作空间的会话仍在执行任务，其系统提示必须继续指向
    自己的工作空间，不能被全局 chdir "带偏"。
    """
    global _current_workspace
    target = Path(path).resolve()
    os.chdir(target)
    _current_workspace = str(target)
    _record_opened_workspace(_current_workspace)
    # Agent 系统提示含 cwd，切换后重建使 Agent 知道新工作空间
    for cs in list(_sessions_by_id.values()) or list(_chat_sessions.values()):
        try:
            if (getattr(cs, "workspace", "") or "") == _current_workspace:
                cs._rebuild_system_prompt()
        except Exception:
            pass


def get_config() -> GlobalConfig:
    global _global_config
    if _global_config is None:
        _global_config = GlobalConfig()
    else:
        # 跨进程配置同步：CLI/Web/Jupyter 任一进程改动后按 mtime 刷新（v5.2.2）
        try:
            _global_config.reload_if_changed()
        except Exception:
            pass
    return _global_config


def get_agent_manager() -> AgentManager:
    global _agent_manager
    if _agent_manager is None:
        config = get_config()
        workspace_base = Path(config.get_settings().get(
            "workspace_base", str(CBHCLI_DIR / "agents")
        ))
        _agent_manager = AgentManager(workspace_base)
    return _agent_manager


def _init_vector_store():
    """懒初始化向量库/嵌入/重排序客户端（只尝试一次）。"""
    global _vector_store, _memory_indexer, _embedding_client, _rerank_client
    global _vector_init_attempted

    if _vector_init_attempted:
        return
    _vector_init_attempted = True

    config = get_config()

    embedding_config = config.get_embedding_model()
    if embedding_config and embedding_config.get("apiKey"):
        try:
            _embedding_client = EmbeddingClient(embedding_config)
        except Exception:
            _embedding_client = None

    rerank_config = config.get_rerank_model()
    if rerank_config and rerank_config.get("apiKey"):
        try:
            _rerank_client = RerankClient(rerank_config)
        except Exception:
            _rerank_client = None

    if _embedding_client:
        try:
            vector_dir = Path.home() / ".cbhcli" / "vectors"
            _vector_store = VectorStore(vector_dir, embedding_client=_embedding_client)
            _memory_indexer = MemoryIndexer(_vector_store)
        except Exception:
            _vector_store = None
            _memory_indexer = None


def _reset_vector_store():
    """嵌入/重排序模型配置变更后调用，下次使用时重建。"""
    global _vector_store, _memory_indexer, _embedding_client, _rerank_client
    global _vector_init_attempted
    _vector_store = None
    _memory_indexer = None
    _embedding_client = None
    _rerank_client = None
    _vector_init_attempted = False


# ===================================================================
#  工具注册表构建
# ===================================================================

# 只读/交互工具 — 无需用户确认
_READONLY_TOOLS = {
    "grep", "glob", "ask_user", "read", "Todo",
    "memory_search", "knowledge_base", "delegate_task", "call_agent",
}


class _WebAgentContext:
    """会话级可变代理，为需要 app 级引用的工具提供上下文。"""

    def __init__(self, agent_name: str):
        self.current_agent_name = agent_name
        self.is_web = True
        self.subagent_scheduler = None
        self.llm_client = None
        self.tool_executor = None
        self.token_counter = None
        self.skill_manager = None
        self.context_compressor = None
        self.context_window = None
        self.auto_compress = True
        self.current_agent_config = None
        # Agent 链条状态
        self.active_chain = None
        self.chain_active_path = None
        self._chain_manager = None
        # ChainExecutor 所需的 app 级引用
        self.agent_manager = None  # 由 WebChatSession.create 设置
        self.global_config = None  # 由 WebChatSession.create 设置
        self.tracer = None         # 由 WebChatSession.create 设置
        self.permission_engine = None  # 由 WebChatSession.create 设置
        # 链条下游 Agent 确认回调（call_agent 执行期间由 _react_loop 设置）
        self._chain_confirm_callback = None
        # 链条下游 Agent ask_user 回调
        self._chain_ask_user_callback = None
        # 链条下游 Agent 事件回调（content/reasoning/tool_call/tool_result）
        self._chain_event_callback = None


class SendFileTool:
    """Web 端专用：向用户发送文件/图片。

    AI 调用此工具将服务器上的文件/图片发送给用户，
    用户在 Web 对话中看到图片内联显示或文件下载链接。
    """

    _IMG_EXTS = {'.png', '.jpg', '.jpeg', '.gif', '.svg', '.webp', '.bmp'}

    @property
    def name(self) -> str:
        return "send_file"

    @property
    def description(self) -> str:
        return (
            "向用户发送文件或图片（仅 Web 界面可用）。"
            "传入文件路径，用户在对话中看到图片内联显示或文件下载链接。\n"
            "适用场景：\n"
            "1. 发送已有图片给用户查看（如 matplotlib 生成的图表）\n"
            "2. 发送文件让用户下载（如配置文件、报告、数据文件等）\n"
            "支持一次发送多个文件。"
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "file_paths": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "要发送的文件绝对路径列表（如 ['/tmp/chart.png', '/tmp/report.csv']）",
                },
            },
            "required": ["file_paths"],
        }

    def execute(self, file_paths: list = None, **kwargs) -> ToolResult:
        if not file_paths:
            return ToolResult(success=False, output="", error="必须提供 file_paths 参数")

        display_files = []
        not_found = []
        for fp_str in file_paths:
            fp = Path(fp_str)
            if not fp.exists() or not fp.is_file():
                not_found.append(fp_str)
                continue
            ext = fp.suffix.lower()
            is_image = ext in self._IMG_EXTS
            display_files.append({
                "path": str(fp.resolve()),
                "filename": fp.name,
                "is_image": is_image,
            })

        if not display_files and not_found:
            return ToolResult(
                success=False, output="",
                error=f"文件不存在: {', '.join(not_found)}")

        output_parts = [f"已发送 {len(display_files)} 个文件给用户："]
        for df in display_files:
            kind = "图片" if df["is_image"] else "文件"
            output_parts.append(f"  📎 [{kind}] {df['filename']} ({df['path']})")
        if not_found:
            output_parts.append(f"⚠️ 以下文件不存在未发送: {', '.join(not_found)}")

        return ToolResult(
            success=True,
            output="\n".join(output_parts),
            display_files=display_files,
        )


def _build_tool_registry(agent_name: str, app_proxy: _WebAgentContext,
                         chain=None) -> ToolRegistry:
    """构建完整工具注册表（内置工具 + cbhpacks + 知识库/记忆/子Agent）。"""
    _init_vector_store()

    registry = ToolRegistry()
    registry.register(TerminalTool())
    registry.register(ReadTool())
    registry.register(WriteTool())
    registry.register(EditTool())
    registry.register(GrepTool())
    registry.register(GlobTool())
    registry.register(AskUserQuestionTool())
    registry.register(TodoTool())
    registry.register(ProcessTool())
    registry.register(KillProcessTool())
    registry.register(PythonTool("default"))

    registry.register(MemorySearchTool(
        vector_store=_vector_store,
        agent_manager=get_agent_manager(),
        app=app_proxy,
    ))
    registry.register(KnowledgeBaseTool(
        vector_store=_vector_store,
        agent_manager=get_agent_manager(),
        rerank_client=_rerank_client,
        app=app_proxy,
    ))
    registry.register(DelegateTaskTool(app_proxy))
    registry.register(SkillsCreateTool(app_proxy))
    registry.register(ImageTool(app_proxy))
    registry.register(SendFileTool())

    # cbhpacks 数据科学工具
    from cbhcli_pkg.tools.cbhpacks_bins import BinsModelTool
    from cbhcli_pkg.tools.cbhpacks_training import BinaryModelTool, UnsModelTool, LinearModelTool
    from cbhcli_pkg.tools.cbhpacks_select import ColsSelectTool, ColsSelectJsTool
    from cbhcli_pkg.tools.cbhpacks_encode import ColsEncodeTool
    from cbhcli_pkg.tools.cbhpacks_preprocess import ColsOperateTool, DescDfTool, DescColTool
    from cbhcli_pkg.tools.cbhpacks_sql import ConSqlTool
    from cbhcli_pkg.tools.cbhpacks_linux import ConLinuxTool
    from cbhcli_pkg.tools.cbhpacks_data import GetRandomDataTool

    registry.register(BinsModelTool())
    registry.register(BinaryModelTool())
    registry.register(UnsModelTool())
    registry.register(LinearModelTool())
    registry.register(ColsSelectTool())
    registry.register(ColsSelectJsTool())
    registry.register(ColsEncodeTool())
    registry.register(ColsOperateTool())
    registry.register(DescDfTool())
    registry.register(DescColTool())
    registry.register(ConSqlTool())
    registry.register(ConLinuxTool())
    registry.register(GetRandomDataTool())

    # 应用 Agent 配置中的禁用工具
    if agent_name:
        config = get_agent_manager().load_agent(agent_name)
        if config and config.disabled_tools:
            registry.set_disabled_tools(config.disabled_tools)

    # 如果激活了链条且当前 Agent 有下游，注册 call_agent 工具
    if chain and agent_name:
        downstream = chain.get_downstream_agents(agent_name)
        if downstream:
            from cbhcli_pkg.tools.call_agent import CallAgentTool
            # app_proxy 需要模拟 chain 执行所需的 app 接口
            app_proxy.active_chain = chain
            app_proxy.chain_active_path = [chain.get_root_agent()]
            registry.register(CallAgentTool(app_proxy, chain, agent_name))

    return registry


# ===================================================================
#  WebChatSession — 统一会话封装
# ===================================================================

class WebChatSession:
    """封装一个 (agent, model) 聊天会话的全部状态。"""

    def __init__(self, agent_name: str, model_name: str):
        self.agent_name = agent_name
        self.model_name = model_name
        self.session_key = f"{agent_name}:{model_name}"

        self.agent_config: Optional[AgentConfig] = None
        self.session: Optional[Session] = None
        self.llm_client: Optional[LLMClient] = None
        self.tool_registry: Optional[ToolRegistry] = None
        self.mcp_manager: Optional[MCPManager] = None
        self.skill_manager: Optional[SkillManager] = None
        self.app_proxy: Optional[_WebAgentContext] = None
        self.context_compressor: Optional[ContextCompressor] = None
        self.context_window: Optional[ContextWindow] = None
        self.token_counter = None

        self.auto_compress = True
        self.no_more_confirmations = False
        self.abort = False

        # Harness 组件
        self.permission_engine: Optional[PermissionEngine] = None
        self.hook_manager: Optional[HookManager] = None
        self.checkpoint_manager: Optional[CheckpointManager] = None
        self.tracer: Optional[Tracer] = None
        self.hook_start_outputs: list = []   # SessionStart 钩子输出（待下发）

        # Agent 链条状态
        self.active_chain = None
        self.chain_active_path = None

        # 会话所属工作空间（v5.2.8，保存历史时写入用于按工作空间分组）
        self.workspace = _current_workspace
        # 自定义会话标题（v5.2.8 侧边栏重命名；为空时用首条用户消息）
        self.custom_title = ""

        # asyncio 原语（在首次使用时绑定到运行中的事件循环）
        self.respond_queue: Optional[asyncio.Queue] = None
        self.lock: Optional[asyncio.Lock] = None

        # ---- v5.2.9：后台运行 + WebSocket 事件广播 ----
        self.run_task: Optional[asyncio.Task] = None   # 后台 ReAct 任务
        self.run_active = False                        # 本次运行是否进行中
        self.run_events: list = []                     # 当前/最近一次运行的事件日志（迟到订阅者回放）
        self.run_seq = 0                               # 会话内单调递增事件序号（跨运行不重置）
        self.run_min_seq = 0                           # 日志裁剪后仍可回放的最小 seq-1
        self.run_start_msg_count = 0                   # 本次运行首条用户消息的下标（运行中导出消息截断到此）
        self.subscribers: list = []                    # 订阅者队列（asyncio.Queue，每个 WebSocket 一个）
        self.respond_waiting = False                   # 是否正等待前端应答（工具确认/ask_user）
        self.last_active_ts: float = 0.0               # 最近一次运行时间戳（空闲会话驱逐排序用）

    # --------------------------------------------------------------
    #  构造
    # --------------------------------------------------------------

    @classmethod
    def create(cls, agent_name: str, model_name: str) -> 'WebChatSession':
        """完整初始化会话（工具/MCP/技能/系统提示/压缩组件）。"""
        config = get_config()
        manager = get_agent_manager()

        model_config = config.get_model(model_name)
        if not model_config:
            raise HTTPException(400, f"模型 '{model_name}' 不存在")
        agent_config = manager.load_agent(agent_name)
        if not agent_config:
            raise HTTPException(400, f"Agent '{agent_name}' 不存在")

        cs = cls(agent_name, model_name)
        cs.agent_config = agent_config
        cs.session = Session(agent_name=agent_name)
        cs.llm_client = LLMClient(model_config)
        cs.token_counter = get_token_counter()
        cs.auto_compress = agent_config.auto_compress
        cs.respond_queue = asyncio.Queue()
        cs.lock = asyncio.Lock()

        # 工具上下文代理
        cs.app_proxy = _WebAgentContext(agent_name)

        # Harness 组件（权限引擎全局单例 + Agent 级钩子/检查点/追踪）
        # 注意：必须在 _rebuild_tools 之前创建（rebuild 时会挂载到 executor）
        cs.permission_engine = get_permission_engine()
        try:
            cs.hook_manager = HookManager(agent_config.workspace_path, agent_name)
        except Exception:
            cs.hook_manager = None
        try:
            cs.checkpoint_manager = CheckpointManager(agent_config.workspace_path)
        except Exception:
            cs.checkpoint_manager = None
        try:
            cs.tracer = Tracer(agent_config.workspace_path, cs.session.id)
        except Exception:
            cs.tracer = None

        cs._rebuild_tools()

        # 注入 delegate_task 所需引用
        cs.app_proxy.llm_client = cs.llm_client
        cs.app_proxy.subagent_scheduler = SubAgentScheduler()
        cs.app_proxy.token_counter = cs.token_counter

        # 技能
        try:
            cs.skill_manager = SkillManager(agent_config.workspace_path)
        except Exception:
            cs.skill_manager = None
        cs.app_proxy.skill_manager = cs.skill_manager

        # 系统提示
        cs._rebuild_system_prompt()

        # 上下文压缩组件
        cs.context_compressor = ContextCompressor(
            cs.llm_client, cs.token_counter,
            workspace_path=getattr(agent_config, "workspace_path", None))
        tools_schema_tokens = cs.token_counter.count_tokens(
            json.dumps(cs.tool_registry.get_openai_tools(), ensure_ascii=False)
        ) if cs.tool_registry.get_openai_tools() else 0
        cs.context_window = ContextWindow(
            model_limit=cs.llm_client.context_limit,
            compression_ratio=agent_config.context_limit_ratio or 0.8,
            tools_schema_tokens=tools_schema_tokens,
        )
        cs.app_proxy.context_compressor = cs.context_compressor
        cs.app_proxy.context_window = cs.context_window
        cs.app_proxy.auto_compress = cs.auto_compress
        cs.app_proxy.current_agent_config = agent_config
        # ChainExecutor 所需引用
        cs.app_proxy.agent_manager = manager
        cs.app_proxy.global_config = config
        cs.app_proxy.tracer = cs.tracer
        cs.app_proxy.permission_engine = cs.permission_engine

        # SessionStart 钩子（输出暂存，首次聊天时通过 SSE 下发）
        if cs.hook_manager and cs.hook_manager.has_hooks("SessionStart"):
            try:
                decision = cs.hook_manager.run_simple(
                    "SessionStart", session_id=cs.session.id)
                cs.hook_start_outputs = decision.outputs
            except Exception:
                pass

        # 恢复持久化的链条激活状态（与 CLI 一致）
        try:
            saved_chain_name = config.get_active_chain(agent_name)
            if saved_chain_name:
                cm = _get_chain_manager()
                saved_chain = cm.get_chain(saved_chain_name)
                if saved_chain:
                    missing = saved_chain.validate(manager)
                    if not missing and saved_chain.get_root_agent() == agent_name:
                        cs.active_chain = saved_chain
                        cs.chain_active_path = [saved_chain.get_root_agent()]
                        cs.app_proxy.active_chain = saved_chain
                        cs.app_proxy.chain_active_path = cs.chain_active_path
                        cs._rebuild_tools()
                        cs._rebuild_system_prompt()
        except Exception:
            pass

        return cs

    def _sync_python_session(self):
        """python 工具解释器会话按聊天会话 id 隔离（v5.2.9 多会话并发）。

        旧版全局共享 "default" 一个解释器，多个会话并发执行 python 时
        变量互相污染；现按 session.id 隔离，会话驱逐/删除时同步清理。
        """
        try:
            pt = self.tool_registry.get("python") if self.tool_registry else None
            if pt is not None and hasattr(pt, "set_session_id"):
                pt.set_session_id(self.session.id if self.session else "default")
        except Exception:
            pass

    def _rebuild_tools(self):
        """重建工具注册表和 MCP 管理器（MCP 配置变更后同步调用）。"""
        self.tool_registry = _build_tool_registry(
            self.agent_name, self.app_proxy, chain=self.active_chain)
        self.app_proxy.tool_executor = ToolExecutor(self.tool_registry)
        self._sync_python_session()

        # Harness 组件挂载（delegate_task 子Agent经 tool_executor 执行工具）
        # 子Agent在后台线程无法交互确认 → 免确认模式，安全由权限引擎
        # deny 红线兜底（子Agent与主会话共享同一套规则）
        executor = self.app_proxy.tool_executor
        executor.no_more_confirmations = True
        executor.animations_enabled = False      # Web 无需终端动画
        executor.permission_engine = get_permission_engine()
        executor.hook_manager = self.hook_manager
        executor.checkpoint_manager = self.checkpoint_manager
        executor.tracer = self.tracer
        executor.session_id = self.session.id if self.session else ""

        self.mcp_manager = None
        try:
            self.mcp_manager = MCPManager(
                self.agent_name, self.agent_config.workspace_path, self.tool_registry
            )
        except Exception:
            pass

        # 更新 tools schema token 数
        if self.context_window and self.token_counter:
            try:
                self.context_window.tools_schema_tokens = self.token_counter.count_tokens(
                    json.dumps(self.tool_registry.get_openai_tools(), ensure_ascii=False)
                )
            except Exception:
                pass

    def rebuild_tools_sync(self):
        """供管理 API 调用：工具开关 / MCP 配置变更后同步会话工具。"""
        try:
            self._rebuild_tools()
        except Exception:
            pass

    def _load_memory_md(self) -> str:
        """读取 memory.md 文件内容（复刻 CLI app.py _load_memory_md 逻辑）。

        跳过模板说明部分（--- 之前），只保留实际记忆内容。
        """
        if not self.agent_config:
            return ""
        memory_file = self.agent_config.workspace_path / "memory.md"
        if memory_file.exists():
            try:
                content = memory_file.read_text(encoding='utf-8')
                lines = content.split('\n')
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

    def _rebuild_system_prompt(self):
        """重建系统提示（首条 system 消息）。"""
        manager = get_agent_manager()
        persona = manager.load_agent_persona(self.agent_name)
        active_skills_prompt = ""
        if self.skill_manager:
            try:
                active_skills_prompt = self.skill_manager.build_skills_prompt()
            except Exception:
                pass
        # 读取长期记忆（与 CLI 一致，memory.md 始终包含在系统提示中）
        memory_content = self._load_memory_md()
        system_prompt = persona.build_system_prompt(
            agent_name=self.agent_name,
            model_name=self.model_name,
            memory_content=memory_content,
            active_skills_prompt=active_skills_prompt,
            cwd=os.getcwd(),
            supports_vision=getattr(self.llm_client, "supports_vision", False),
        )
        # 注入当前权限模式说明（与 CLI 一致）
        system_prompt += build_mode_note(get_permission_engine().mode)
        # 注入 Agent 链条信息（如果激活了链条）
        if self.active_chain and self.agent_name:
            from cbhcli_pkg.core.agent_chain import build_chain_prompt
            chain_prompt = build_chain_prompt(
                self.active_chain, get_agent_manager(), self.agent_name
            )
            if chain_prompt:
                system_prompt += chain_prompt
        # 注入 Web 界面特有功能说明（仅 Web 端，CLI 不注入）
        system_prompt += (
            "\n\n## Web 界面功能\n"
            "你当前运行在 Web 界面中，以下功能仅 Web 端可用：\n"
            "\n"
            "### 用户上传文件\n"
            "- 用户可以通过输入框左侧的 📎 按钮或直接粘贴来上传图片和文件\n"
            "- 上传的图片会直接以图片形式显示在对话中，你可以用 image 工具识别\n"
            "- 上传的文件会保存到服务器，你可以用 read 工具读取文件内容\n"
            "- 当用户要求查看/处理某个文件时，提醒用户可以点击 📎 按钮上传\n"
            "- 用户发送的文件路径通常在 ~/.cbhcli/web_uploads/ 目录下\n"
            "\n"
            "### AI 发送文件/图片给用户\n"
            "你可以主动向用户发送文件和图片，用户会在对话中看到并可以下载：\n"
            "- **发送文件/图片**：使用 `send_file` 工具，传入文件绝对路径列表，"
            "图片会内联显示（点击可放大），文件会显示下载链接。\n"
            "  示例：send_file(file_paths=['/tmp/chart.png', '/tmp/report.csv'])\n"
            "- **python 生成图片自动展示**：用 python 工具 matplotlib 等生成图片后，"
            "系统也会自动检测并展示（无需手动调 send_file）。\n"
            "- **write/edit 写文件自动下载链接**：write/edit 工具成功后系统自动生成下载链接。\n"
            "- 当用户要求查看某个文件/图片时，直接用 send_file 发送即可。\n"
        )
        # 替换或插入首条 system 消息
        if self.session.messages and self.session.messages[0].role == "system":
            self.session.messages[0].content = system_prompt
        else:
            self.session.add_message("system", system_prompt)

    # --------------------------------------------------------------
    #  应答等待（工具确认 / ask_user）
    # --------------------------------------------------------------

    async def wait_response(self, timeout: float = 600.0) -> Optional[str]:
        """等待前端应答，支持中断。返回 None 表示超时或被中断。

        v5.2.9：respond_waiting 标志控制 /api/chat/respond 是否接收应答--
        多个浏览器同时显示确认框时，仅会话真正在等待期间的首个应答生效，
        迟到的应答被拒绝（避免误当作下一次确认的结果）。
        """
        self.respond_waiting = True
        try:
            # 清空过期应答
            while not self.respond_queue.empty():
                try:
                    self.respond_queue.get_nowait()
                except asyncio.QueueEmpty:
                    break

            elapsed = 0.0
            interval = 0.5
            while elapsed < timeout:
                if self.abort:
                    return None
                try:
                    return await asyncio.wait_for(self.respond_queue.get(), timeout=interval)
                except asyncio.TimeoutError:
                    elapsed += interval
            return None
        finally:
            self.respond_waiting = False

    # --------------------------------------------------------------
    #  状态导出
    # --------------------------------------------------------------

    def usage_stats(self) -> dict:
        total = self.session.get_total_tokens(self.token_counter)
        self.context_window.update(total)
        return {
            "token_estimate": total,
            "model_limit": self.context_window.model_limit,
            "ctx_percentage": round(self.context_window.usage_percentage() * 100, 1),
            "remaining_tokens": self.context_window.remaining_tokens(),
            "message_count": len(self.session.messages),
            "tool_call_count": self.session.tool_call_count,
            "auto_compress": self.auto_compress,
            "compression_ratio": self.context_window.compression_ratio,
        }

    def export_messages(self, limit: Optional[int] = None) -> list[dict]:
        """导出为前端可恢复的展示结构（assistant 消息聚合 tool 结果）。

        limit（v5.2.9）：会话正在后台运行时传 run_start_msg_count，
        截断掉本次运行中已产生的消息--这部分由 WebSocket 事件日志回放
        渲染（含正在流式输出的内容），避免双份展示。
        """
        result = []
        messages = self.session.messages
        if limit is not None and limit >= 0:
            messages = messages[:limit]
        for m in messages:
            if m.role == "system":
                continue
            if m.role == "user":
                # 解析用户消息中的文件信息（[图片: xxx] / [文件: xxx (path)]）
                image_urls = []
                file_attachments = []
                content = m.content or ""
                import re as _re
                for match in _re.finditer(r'\[图片: (.+?)\]', content):
                    fname = match.group(1).strip()
                    image_urls.append(f"/api/files/serve/{fname}")
                for match in _re.finditer(r'\[文件: (.+?) \((.+?)\)\]', content):
                    fname = match.group(1).strip()
                    fpath = match.group(2).strip()
                    # 从 web_uploads 目录提取文件名
                    upload_dir = CBHCLI_DIR / "web_uploads"
                    try:
                        rel = str(Path(fpath)).replace(str(upload_dir) + "/", "")
                        rel = rel.replace(str(upload_dir) + "\\", "")
                        if rel == str(Path(fpath)):
                            # 路径不在 web_uploads，使用文件名
                            rel = Path(fpath).name
                    except Exception:
                        rel = Path(fpath).name
                    file_attachments.append({
                        "filename": fname,
                        "download_url": f"/api/files/download/{rel}",
                        "path": fpath,
                    })
                result.append({
                    "role": "user",
                    "content": content,
                    "image_count": len(m.images) if m.images else 0,
                    "image_urls": image_urls,
                    "file_attachments": file_attachments,
                })
            elif m.role == "assistant":
                tool_calls = []
                if m.tool_calls:
                    for tc in m.tool_calls:
                        func = tc.get("function", {})
                        try:
                            args = json.loads(func.get("arguments", "{}"))
                            args = _fix_unicode_escapes(args)
                        except Exception:
                            args = {}
                        tool_calls.append({
                            "id": tc.get("id", ""),
                            "name": func.get("name", ""),
                            "arguments": args,
                            "result": None,
                            "success": None,
                        })
                result.append({
                    "role": "assistant",
                    "content": m.content or "",
                    "reasoning": m.reasoning_content or "",
                    "tool_calls": tool_calls,
                })
            elif m.role == "tool":
                # 挂到最近一条 assistant 的对应 tool_call 上
                tcid = m.tool_call_id or ""
                for item in reversed(result):
                    if item["role"] != "assistant":
                        continue
                    for tc in item["tool_calls"]:
                        if tc["id"] == tcid:
                            tc["result"] = (m.content or "")[:2000]
                            tc["success"] = (m.metadata or {}).get("success")
                            break
                    break
        return result


def _get_session_key(agent_name: str, model_name: str) -> str:
    return f"{agent_name}:{model_name}"


def _register_session(cs: 'WebChatSession') -> None:
    """注册到全局会话注册表并按需驱逐空闲会话（v5.2.9）。"""
    if cs is None or not cs.session:
        return
    _sessions_by_id[cs.session.id] = cs
    _evict_idle_sessions()


def _evict_idle_sessions() -> None:
    """存活会话超上限时驱逐最旧的空闲会话（无订阅者/未运行/非默认键）。

    每轮对话结束已自动落盘（v5.2.6 autosave），驱逐不丢数据；
    驱逐同步清理对应的 python 解释器会话。
    """
    if len(_sessions_by_id) <= _MAX_LIVE_SESSIONS:
        return
    default_sessions = set(id(cs) for cs in _chat_sessions.values())
    candidates = [
        cs for cs in _sessions_by_id.values()
        if not cs.run_active
        and not cs.subscribers
        and id(cs) not in default_sessions
    ]
    candidates.sort(key=lambda cs: getattr(cs, "last_active_ts", 0.0))
    while len(_sessions_by_id) > _MAX_LIVE_SESSIONS and candidates:
        victim = candidates.pop(0)
        _sessions_by_id.pop(victim.session.id, None)
        try:
            remove_python_session(victim.session.id)
        except Exception:
            pass


def _find_history_file_by_id(agent_name: str, session_id: str) -> Optional[str]:
    """按会话 id 在历史目录定位文件名（服务器重启后找回会话用）。"""
    if not session_id or any(c in session_id for c in "*?[]"):
        return None
    try:
        agent_config = _get_agent_config(agent_name)
        if not agent_config:
            return None
        hist_dir = SessionHistoryManager(agent_config.workspace_path).history_dir
        found = sorted(hist_dir.glob(f"*_{session_id}.json"), reverse=True)
        return found[0].name if found else None
    except Exception:
        return None


def _resolve_session(agent_name: str, model_name: str,
                     session_id: str = "") -> 'WebChatSession':
    """按 session_id 解析会话；无 id 或未命中时回落到 (agent, model) 默认会话。

    v5.2.9：会话身份以 session.id 为准（同一 agent:model 下可并存多个会话）。
    服务器重启导致内存会话丢失时，自动按 id 从历史恢复。
    """
    if session_id:
        cs = _sessions_by_id.get(session_id)
        if cs is not None and cs.agent_name == agent_name:
            return cs
        # 内存未命中：尝试从历史按 id 恢复（服务器重启场景）
        filename = _find_history_file_by_id(agent_name, session_id)
        if filename:
            try:
                cs, _ = _load_session_core(agent_name, model_name, filename)
                return cs
            except Exception:
                pass
        # 历史也无此 id（从未落盘的新会话）：回落默认会话（可能是新建）
        return _get_or_create_session(agent_name, model_name)
    return _get_or_create_session(agent_name, model_name)


def _resolve_session_quiet(agent_name: str, model_name: str,
                           session_id: str = "") -> Optional['WebChatSession']:
    """同 _resolve_session，但不创建新会话（查询/应答类端点用）。"""
    if session_id:
        cs = _sessions_by_id.get(session_id)
        if cs is not None and cs.agent_name == agent_name:
            return cs
        return _chat_sessions.get(_get_session_key(agent_name, model_name))
    return _chat_sessions.get(_get_session_key(agent_name, model_name))


def _get_or_create_session(agent_name: str, model_name: str) -> WebChatSession:
    key = _get_session_key(agent_name, model_name)
    cs = _chat_sessions.get(key)
    if cs is None:
        cs = WebChatSession.create(agent_name, model_name)
        import time as _time
        cs.last_active_ts = _time.time()
        _chat_sessions[key] = cs
        _register_session(cs)
    return cs


def _sync_agent_tool_change(agent_name: str, full_rebuild: bool = False):
    """工具开关/MCP 变更后同步到该 Agent 的所有存活会话（含后台运行的）。"""
    seen = set()
    for cs in list(_sessions_by_id.values()) + list(_chat_sessions.values()):
        if id(cs) in seen or cs.agent_name != agent_name:
            continue
        seen.add(id(cs))
        try:
            if full_rebuild:
                cs.rebuild_tools_sync()
            else:
                config = get_agent_manager().load_agent(agent_name)
                if config and cs.tool_registry:
                    cs.tool_registry.set_disabled_tools(config.disabled_tools or [])
        except Exception:
            pass


def _get_agent_workspace(agent_name: str) -> Path:
    manager = get_agent_manager()
    config = manager.load_agent(agent_name)
    if not config:
        raise HTTPException(404, f"Agent '{agent_name}' 不存在")
    return config.workspace_path


def _get_agent_config(agent_name: str) -> Optional[AgentConfig]:
    return get_agent_manager().load_agent(agent_name)


def _save_agent_config(config: AgentConfig):
    get_agent_manager()._save_config(config)


def _get_mcp_manager(agent_name: str) -> MCPManager:
    """管理 API 专用 MCP 管理器（带独立注册表，按 Agent 缓存）。"""
    if agent_name not in _mcp_managers:
        workspace = _get_agent_workspace(agent_name)
        _mcp_managers[agent_name] = MCPManager(agent_name, workspace, ToolRegistry())
    else:
        # 跨进程 MCP 同步：其他进程改动 mcp.json 后按 mtime 刷新（v5.2.2）
        try:
            _mcp_managers[agent_name].reload_if_changed()
        except Exception:
            pass
    return _mcp_managers[agent_name]


def _invalidate_mcp_manager(agent_name: str):
    _mcp_managers.pop(agent_name, None)


# ===================================================================
#  Pydantic Models
# ===================================================================

class ModelConfig(BaseModel):
    name: str
    apiKey: str
    url: str
    model: str
    context_limit: int = 128000
    vision: bool = False
    temperature: Optional[float] = None  # 模型专属温度（None 使用全局默认）
    max_tokens: Optional[int] = None  # 最大输出token数（None 使用API默认值）
    thinking: Optional[bool] = None  # 思考模式参数（None 不传给 API）
    reasoning_effort: Optional[str] = None  # 推理强度（None 不传给 API，如 low/medium/high）

    def to_config_dict(self) -> dict:
        """转为存储配置（None 字段不写入，避免覆盖已有配置）。"""
        return self.model_dump(exclude_none=True)


class EmbeddingModelConfig(BaseModel):
    name: str = ""
    apiKey: str = ""
    url: str = ""
    model: str = ""


class RerankModelConfig(BaseModel):
    name: str = ""
    apiKey: str = ""
    url: str = ""
    model: str = ""
    top_n: int = 5


class AgentCreate(BaseModel):
    name: str
    description: str = ""
    primary_model: Optional[str] = None


class AgentUpdate(BaseModel):
    description: Optional[str] = None
    primary_model: Optional[str] = None
    context_limit_ratio: Optional[float] = None
    auto_compress: Optional[bool] = None
    max_tool_calls: Optional[int] = None


class ChatRequest(BaseModel):
    message: str
    agent_name: str
    model_name: str
    images: list[str] = []
    file_infos: list[dict] = []  # [{filename, url, download_url, is_image, size, content_type}]
    session_id: str = ""        # v5.2.9：目标会话 id（空=该 agent:model 的默认会话）


class ChatRespondRequest(BaseModel):
    agent_name: str
    model_name: str
    response: str
    session_id: str = ""        # v5.2.9：应答目标会话 id


class FileContent(BaseModel):
    content: str


class SettingsUpdate(BaseModel):
    auto_compress: Optional[bool] = None
    compression_ratio: Optional[float] = None


class MCPServerAdd(BaseModel):
    name: str
    url: str
    headers: Optional[dict] = None
    enabled_tools: Optional[list[str]] = None


class SkillActivate(BaseModel):
    names: list[str]


class KnowledgeAdd(BaseModel):
    file_path: str


class Toggle(BaseModel):
    enable: bool


class FallbackAdd(BaseModel):
    category: str  # main | vision
    model_name: str


class FallbackReorder(BaseModel):
    order: list[str]


# ===================================================================
#  API: System Info / Settings
# ===================================================================

@app.get("/api/info")
def get_info():
    from cbhcli_pkg import __version__
    config = get_config()
    manager = get_agent_manager()
    return {
        "version": __version__,
        "config_dir": str(CBHCLI_DIR),
        "agents_count": len(manager.list_agents()),
        "models_count": len(config.get_models()),
        "active_agent": config.get_active_agent(),
        "last_model": config.get_last_selected_model(),
    }


@app.get("/api/settings")
def get_settings():
    config = get_config()
    return {
        "settings": config.get_settings(),
        "config_dir": str(CBHCLI_DIR),
    }


@app.put("/api/settings")
def update_settings(update: SettingsUpdate):
    config = get_config()
    for key, val in update.model_dump(exclude_none=True).items():
        config.update_setting(key, val)
    return {"message": "设置已更新"}


# ===================================================================
#  API: Model Management
# ===================================================================

@app.get("/api/models")
def list_models():
    config = get_config()
    return {
        "models": config.get_models(),
        "last_selected": config.get_last_selected_model(),
        "embedding_model": config.get_embedding_model(),
        "rerank_model": config.get_rerank_model(),
    }


@app.post("/api/models")
def add_model(model: ModelConfig):
    config = get_config()
    if config.get_model(model.name):
        raise HTTPException(400, f"模型 '{model.name}' 已存在")
    # thinking=off 时不能配置 reasoning_effort（DeepSeek 等 API 报 400）
    if model.thinking is False and model.reasoning_effort:
        raise HTTPException(
            400, "thinking=off 时不能配置 reasoning_effort（API 会返回 400 错误），"
                 "请清除 reasoning_effort 或开启 thinking")
    config.add_model(model.to_config_dict())
    return {"message": f"模型 '{model.name}' 已添加"}


@app.put("/api/models/embedding")
def update_embedding_model(model: EmbeddingModelConfig):
    config = get_config()
    config.set_embedding_model(model.model_dump())
    _reset_vector_store()
    return {"message": "嵌入模型已更新"}


@app.delete("/api/models/embedding")
def delete_embedding_model():
    config = get_config()
    config.delete_embedding_model()
    _reset_vector_store()
    return {"message": "嵌入模型已删除"}


@app.put("/api/models/rerank")
def update_rerank_model(model: RerankModelConfig):
    config = get_config()
    config.set_rerank_model(model.model_dump())
    _reset_vector_store()
    return {"message": "重排序模型已更新"}


@app.delete("/api/models/rerank")
def delete_rerank_model():
    config = get_config()
    config.delete_rerank_model()
    _reset_vector_store()
    return {"message": "重排序模型已删除"}


@app.put("/api/models/{model_name}")
def update_model(model_name: str, model: ModelConfig):
    config = get_config()
    models = config.get_models()
    for i, m in enumerate(models):
        if m.get("name") == model_name:
            # thinking=off 时不能配置 reasoning_effort（DeepSeek 等 API 报 400）
            if model.thinking is False and model.reasoning_effort:
                raise HTTPException(
                    400, "thinking=off 时不能配置 reasoning_effort（API 会返回 400 错误），"
                         "请清除 reasoning_effort 或开启 thinking")
            models[i] = model.to_config_dict()
            config.save()
            return {"message": f"模型 '{model_name}' 已更新"}
    raise HTTPException(404, f"模型 '{model_name}' 不存在")


@app.delete("/api/models/{model_name}")
def delete_model(model_name: str):
    config = get_config()
    if config.delete_model(model_name):
        return {"message": f"模型 '{model_name}' 已删除"}
    raise HTTPException(404, f"模型 '{model_name}' 不存在")


@app.post("/api/models/{model_name}/select")
def select_model(model_name: str):
    config = get_config()
    if not config.get_model(model_name):
        raise HTTPException(404, f"模型 '{model_name}' 不存在")
    config.set_last_selected_model(model_name)
    return {"message": f"已选择模型 '{model_name}'"}


# ===================================================================
#  API: Fallback Models (备用模型)
# ===================================================================

@app.get("/api/fallback")
def get_fallback():
    config = get_config()
    models = config.get_models()
    return {
        "main": config.get_fallback_models(),
        "vision": config.get_fallback_vision_models(),
        "available_models": [
            {"name": m.get("name", ""), "vision": m.get("vision", False)}
            for m in models
        ],
    }


@app.post("/api/fallback")
def add_fallback(body: FallbackAdd):
    config = get_config()
    category = body.category.lower()
    if category not in ("main", "vision"):
        raise HTTPException(400, "类别必须是 main 或 vision")

    model = config.get_model(body.model_name)
    if not model:
        raise HTTPException(404, f"模型 '{body.model_name}' 不存在")
    if category == "vision" and not model.get("vision", False):
        raise HTTPException(400, f"模型 '{body.model_name}' 不支持视觉功能")

    lst = config.get_fallback_models() if category == "main" else config.get_fallback_vision_models()
    if body.model_name in lst:
        raise HTTPException(400, f"模型 '{body.model_name}' 已在备用列表中")

    lst.append(body.model_name)
    if category == "main":
        config.set_fallback_models(lst)
    else:
        config.set_fallback_vision_models(lst)
    return {"message": f"已添加 '{body.model_name}' 到 {category} 备用列表"}


@app.delete("/api/fallback/{category}")
def clear_fallback(category: str):
    config = get_config()
    category = category.lower()
    if category == "main":
        config.set_fallback_models([])
    elif category == "vision":
        config.set_fallback_vision_models([])
    else:
        raise HTTPException(400, "类别必须是 main 或 vision")
    return {"message": f"已清空 {category} 备用列表"}


@app.delete("/api/fallback/{category}/{model_name}")
def remove_fallback(category: str, model_name: str):
    config = get_config()
    category = category.lower()
    if category not in ("main", "vision"):
        raise HTTPException(400, "类别必须是 main 或 vision")

    lst = config.get_fallback_models() if category == "main" else config.get_fallback_vision_models()
    if model_name not in lst:
        raise HTTPException(404, f"模型 '{model_name}' 不在备用列表中")
    lst.remove(model_name)
    if category == "main":
        config.set_fallback_models(lst)
    else:
        config.set_fallback_vision_models(lst)
    return {"message": f"已移除 '{model_name}'"}


@app.put("/api/fallback/{category}/reorder")
def reorder_fallback(category: str, body: FallbackReorder):
    config = get_config()
    category = category.lower()
    if category not in ("main", "vision"):
        raise HTTPException(400, "类别必须是 main 或 vision")

    lst = config.get_fallback_models() if category == "main" else config.get_fallback_vision_models()
    if sorted(body.order) != sorted(lst):
        raise HTTPException(400, "新顺序必须包含且仅包含当前列表中的所有模型")
    if category == "main":
        config.set_fallback_models(list(body.order))
    else:
        config.set_fallback_vision_models(list(body.order))
    return {"message": f"{category} 备用顺序已更新"}


# ===================================================================
#  API: Harness（权限模式 / 权限规则 / 钩子）
# ===================================================================

class ModeUpdate(BaseModel):
    mode: str


class PermissionRuleUpdate(BaseModel):
    action: str      # add | rm
    category: str    # allow | ask | deny
    rule: str


def _permission_state() -> dict:
    """权限引擎当前状态（供多个端点复用）"""
    engine = get_permission_engine()
    rules = engine.get_user_rules()
    return {
        "mode": engine.mode,
        "default_mode": engine._default_mode,
        "yolo_keep_deny": engine.yolo_keep_deny,
        "modes": [
            {"id": m, "label": MODE_META[m]["label"],
             "icon": MODE_META[m]["icon"], "desc": MODE_META[m]["desc"],
             "current": m == engine.mode}
            for m in MODES
        ],
        "rules": rules,
    }


@app.get("/api/permissions")
def get_permissions():
    """获取权限模式与规则（对应 CLI /mode list + /permissions list）"""
    return _permission_state()


@app.post("/api/permissions/mode")
def set_permission_mode(body: ModeUpdate):
    """切换权限模式（对应 CLI /mode <模式>，同步全部活跃会话系统提示）"""
    engine = get_permission_engine()
    if body.mode not in MODES:
        raise HTTPException(400, f"未知模式: {body.mode}，可用: {MODES}")
    old_mode = engine.mode
    engine.set_mode(body.mode)
    # 同步所有活跃会话的系统提示（模式说明注入）
    for cs in _chat_sessions.values():
        try:
            cs._rebuild_system_prompt()
        except Exception:
            pass
        if cs.tracer:
            cs.tracer.log_mode_change(old_mode, body.mode)
    return {"message": f"权限模式: {old_mode} → {body.mode}",
            **_permission_state()}


@app.post("/api/permissions/rules")
def update_permission_rule(body: PermissionRuleUpdate):
    """添加/删除用户权限规则（对应 CLI /permissions add|rm）"""
    engine = get_permission_engine()
    if body.category not in ("allow", "ask", "deny"):
        raise HTTPException(400, "类别必须是 allow / ask / deny")
    if body.action == "add":
        engine.add_rule(body.category, body.rule)
        return {"message": f"已添加 {body.category} 规则: {body.rule}",
                **_permission_state()}
    if body.action == "rm":
        if engine.remove_rule(body.category, body.rule):
            return {"message": f"已删除 {body.category} 规则: {body.rule}",
                    **_permission_state()}
        raise HTTPException(404, f"规则不存在: {body.rule}")
    raise HTTPException(400, "action 必须是 add / rm")


@app.get("/api/hooks/{agent_name}")
def get_hooks(agent_name: str):
    """查看 Agent 钩子配置（对应 CLI /hooks list）"""
    workspace = _get_agent_workspace(agent_name)
    hm = HookManager(workspace, agent_name)
    return {"hooks": hm.get_hooks()}


@app.post("/api/hooks/{agent_name}/reload")
def reload_hooks(agent_name: str):
    """重载钩子配置并同步到该 Agent 的活跃会话（对应 CLI /hooks reload）"""
    count = 0
    for cs in _chat_sessions.values():
        if cs.agent_name == agent_name and cs.hook_manager:
            try:
                cs.hook_manager.reload()
                count += 1
            except Exception:
                pass
    return {"message": f"钩子配置已重载（同步 {count} 个活跃会话）"}


# ===================================================================
#  API: 检查点回滚（/undo）
# ===================================================================

class UndoRequest(BaseModel):
    backup_id: Optional[str] = None


@app.get("/api/agents/{agent_name}/backups")
def list_backups(agent_name: str):
    """列出可回滚的文件备份（对应 CLI /undo list）"""
    workspace = _get_agent_workspace(agent_name)
    cm = CheckpointManager(workspace)
    return {"backups": cm.list_backups(50)}


@app.post("/api/agents/{agent_name}/undo")
def undo_backup(agent_name: str, body: UndoRequest):
    """回滚文件修改（对应 CLI /undo [ID]）"""
    workspace = _get_agent_workspace(agent_name)
    cm = CheckpointManager(workspace)
    if body.backup_id:
        ok, msg = cm.undo_by_id(body.backup_id)
    else:
        ok, msg = cm.undo_last()
    if not ok:
        raise HTTPException(400, msg)
    return {"message": msg}


# ===================================================================
#  API: Agent Management
# ===================================================================

@app.get("/api/agents")
def list_agents():
    manager = get_agent_manager()
    config = get_config()
    agents = manager.list_agents()
    return {
        "agents": [a.to_dict() for a in agents],
        "active_agent": config.get_active_agent(),
    }


@app.post("/api/agents")
def create_agent(agent: AgentCreate):
    manager = get_agent_manager()
    if manager.load_agent(agent.name):
        raise HTTPException(400, f"Agent '{agent.name}' 已存在")
    config = manager.create_agent(
        name=agent.name,
        description=agent.description,
        primary_model=agent.primary_model,
    )
    return {"message": f"Agent '{agent.name}' 已创建", "agent": config.to_dict()}


@app.get("/api/agents/{agent_name}")
def get_agent(agent_name: str):
    manager = get_agent_manager()
    config = manager.load_agent(agent_name)
    if not config:
        raise HTTPException(404, f"Agent '{agent_name}' 不存在")

    workspace = config.workspace_path
    files = {}
    for fname in ["soul.md", "tools.md", "memory.md", "usage.md"]:
        fpath = workspace / fname
        if fpath.exists():
            files[fname] = fpath.read_text(encoding="utf-8")

    skills = []
    skills_dir = workspace / "skills"
    if skills_dir.exists():
        for d in skills_dir.iterdir():
            if d.is_dir() and (d / "skills.md").exists():
                skills.append({
                    "name": d.name,
                    "content": (d / "skills.md").read_text(encoding="utf-8")[:200],
                })

    mcp_servers = []
    mcp_config_file = workspace / "mcp.json"
    if mcp_config_file.exists():
        try:
            data = json.loads(mcp_config_file.read_text(encoding="utf-8"))
            mcp_servers = data.get("servers", [])
        except Exception:
            pass

    return {
        "config": config.to_dict(),
        "files": files,
        "skills": skills,
        "mcp_servers": mcp_servers,
    }


@app.put("/api/agents/{agent_name}")
def update_agent(agent_name: str, update: AgentUpdate):
    manager = get_agent_manager()
    config = manager.load_agent(agent_name)
    if not config:
        raise HTTPException(404, f"Agent '{agent_name}' 不存在")

    data = config.to_dict()
    for key, val in update.model_dump(exclude_none=True).items():
        data[key] = val

    new_config = AgentConfig.from_dict(data, config.workspace_path)
    config_file = config.workspace_path / "config.json"
    with open(config_file, "w", encoding="utf-8") as f:
        json.dump(new_config.to_dict(), f, indent=2, ensure_ascii=False)

    return {"message": f"Agent '{agent_name}' 已更新"}


@app.delete("/api/agents/{agent_name}")
def delete_agent(agent_name: str):
    manager = get_agent_manager()
    # 清理该 Agent 的活跃会话与 MCP 管理器缓存
    for key in [k for k, cs in _chat_sessions.items() if cs.agent_name == agent_name]:
        del _chat_sessions[key]
    _invalidate_mcp_manager(agent_name)

    if manager.delete_agent(agent_name):
        return {"message": f"Agent '{agent_name}' 已删除"}
    raise HTTPException(404, f"Agent '{agent_name}' 不存在")


@app.post("/api/agents/{agent_name}/select")
def select_agent(agent_name: str):
    manager = get_agent_manager()
    config = get_config()
    if not manager.load_agent(agent_name):
        raise HTTPException(404, f"Agent '{agent_name}' 不存在")
    config.set_active_agent(agent_name)
    return {"message": f"已切换到 Agent '{agent_name}'"}


@app.put("/api/agents/{agent_name}/files/{filename}")
def update_agent_file(agent_name: str, filename: str, body: FileContent):
    config = _get_agent_config(agent_name)
    if not config:
        raise HTTPException(404, f"Agent '{agent_name}' 不存在")

    allowed = {"soul.md", "tools.md", "memory.md", "usage.md"}
    if filename not in allowed:
        raise HTTPException(400, f"不允许编辑文件: {filename}")

    fpath = config.workspace_path / filename
    fpath.write_text(body.content, encoding="utf-8")

    # 系统提示包含这些文件内容，同步刷新活跃会话
    for cs in _chat_sessions.values():
        if cs.agent_name == agent_name:
            try:
                cs._rebuild_system_prompt()
            except Exception:
                pass

    return {"message": f"{filename} 已更新"}


# ===================================================================
#  API: Skills Management
# ===================================================================

@app.get("/api/agents/{agent_name}/skills")
def list_skills(agent_name: str):
    workspace = _get_agent_workspace(agent_name)
    sm = SkillManager(workspace)
    skills = sm.list_skills()
    active_names = sm.get_active_skill_names()
    return {
        "skills": [
            {
                "name": s.name,
                "active": s.name in active_names,
                "has_scripts": s.has_scripts,
                "scripts": s.list_scripts(),
                "prompt": s.prompt or "",
                "prompt_preview": (s.prompt or "")[:300],
            }
            for s in skills
        ],
        "active": active_names,
    }


@app.post("/api/agents/{agent_name}/skills/activate")
def activate_skills(agent_name: str, body: SkillActivate):
    workspace = _get_agent_workspace(agent_name)
    sm = SkillManager(workspace)
    activated = sm.activate_skills(body.names)
    return {"message": f"已激活 {len(activated)} 个技能", "activated": activated}


@app.post("/api/agents/{agent_name}/skills/{skill_name}/deactivate")
def deactivate_skill(agent_name: str, skill_name: str):
    workspace = _get_agent_workspace(agent_name)
    sm = SkillManager(workspace)
    if sm.deactivate_skill(skill_name):
        return {"message": f"已取消激活技能 '{skill_name}'"}
    raise HTTPException(404, f"技能 '{skill_name}' 不存在或未激活")


@app.delete("/api/agents/{agent_name}/skills/{skill_name}")
def delete_skill(agent_name: str, skill_name: str):
    workspace = _get_agent_workspace(agent_name)
    sm = SkillManager(workspace)
    if sm.remove_skill(skill_name):
        return {"message": f"技能 '{skill_name}' 已删除"}
    raise HTTPException(404, f"技能 '{skill_name}' 不存在")


# ===================================================================
#  API: MCP Management（真实连接/刷新，与 CLI 一致）
# ===================================================================

@app.get("/api/agents/{agent_name}/mcp")
def list_mcp_servers(agent_name: str):
    manager = _get_mcp_manager(agent_name)
    return {"servers": manager.list_servers()}


@app.post("/api/agents/{agent_name}/mcp")
def add_mcp_server(agent_name: str, body: MCPServerAdd):
    manager = _get_mcp_manager(agent_name)
    msg = manager.add_server(
        name=body.name, url=body.url,
        headers=body.headers, enabled_tools=body.enabled_tools,
    )
    if "❌" in msg:
        raise HTTPException(400, msg)
    _sync_agent_tool_change(agent_name, full_rebuild=True)
    return {"message": msg}


@app.delete("/api/agents/{agent_name}/mcp/{server_name}")
def remove_mcp_server(agent_name: str, server_name: str):
    manager = _get_mcp_manager(agent_name)
    msg = manager.remove_server(server_name)
    if "❌" in msg:
        raise HTTPException(404, msg)
    _sync_agent_tool_change(agent_name, full_rebuild=True)
    return {"message": msg}


@app.post("/api/agents/{agent_name}/mcp/{server_name}/refresh")
def refresh_mcp_server(agent_name: str, server_name: str):
    manager = _get_mcp_manager(agent_name)
    msg = manager.refresh_server(server_name)
    if "❌" in msg:
        raise HTTPException(404, msg)
    _sync_agent_tool_change(agent_name, full_rebuild=True)
    return {"message": msg}


@app.get("/api/agents/{agent_name}/mcp/{server_name}/tools")
def list_mcp_server_tools(agent_name: str, server_name: str):
    manager = _get_mcp_manager(agent_name)
    tools = manager.get_all_server_tools(server_name)
    return {"tools": tools}


@app.put("/api/agents/{agent_name}/mcp/{server_name}/tools/{tool_name}")
def toggle_mcp_tool(agent_name: str, server_name: str, tool_name: str, body: Toggle):
    manager = _get_mcp_manager(agent_name)
    msg = manager.toggle_tool(server_name, tool_name, body.enable)
    if "❌" in msg:
        raise HTTPException(400, msg)
    _sync_agent_tool_change(agent_name, full_rebuild=True)
    return {"message": msg}


# ===================================================================
#  API: Tools Management
# ===================================================================

@app.get("/api/agents/{agent_name}/tools")
def list_tools(agent_name: str):
    from cbhcli_pkg.commands.tools_cmd import BUILTIN_TOOLS
    config = _get_agent_config(agent_name)
    if not config:
        raise HTTPException(404, f"Agent '{agent_name}' 不存在")
    disabled = config.disabled_tools or []

    tools = []
    for name, desc, category in BUILTIN_TOOLS:
        tools.append({
            "name": name,
            "description": desc,
            "category": category,
            "enabled": name not in disabled,
        })
    return {"tools": tools, "disabled": disabled}


@app.put("/api/agents/{agent_name}/tools/{tool_name}")
def toggle_tool(agent_name: str, tool_name: str, body: Toggle):
    config = _get_agent_config(agent_name)
    if not config:
        raise HTTPException(404, f"Agent '{agent_name}' 不存在")

    disabled = list(config.disabled_tools or [])
    if body.enable:
        if tool_name in disabled:
            disabled.remove(tool_name)
    else:
        if tool_name not in disabled:
            disabled.append(tool_name)

    config.disabled_tools = disabled
    _save_agent_config(config)
    _sync_agent_tool_change(agent_name, full_rebuild=False)

    action = "启用" if body.enable else "禁用"
    return {"message": f"工具 '{tool_name}' 已{action}", "disabled": disabled}


# ===================================================================
#  API: Knowledge Base Management
# ===================================================================

def _get_kb(agent_name: str):
    _init_vector_store()
    _get_agent_workspace(agent_name)
    from cbhcli_pkg.core.knowledge_base import KnowledgeBase
    return KnowledgeBase(agent_name, vector_store=_vector_store, indexer=_memory_indexer)


@app.get("/api/agents/{agent_name}/knowledge")
def list_knowledge(agent_name: str):
    kb = _get_kb(agent_name)
    return {"files": kb.list_files(), "vector_enabled": _vector_store is not None}


@app.post("/api/agents/{agent_name}/knowledge")
def add_knowledge_file(agent_name: str, body: KnowledgeAdd):
    kb = _get_kb(agent_name)
    result = kb.add_file(body.file_path)
    if not result.get("success"):
        raise HTTPException(400, result.get("message", "添加失败"))
    return result


@app.post("/api/agents/{agent_name}/knowledge/upload")
async def upload_knowledge_file(agent_name: str, file: UploadFile = File(...)):
    _get_agent_workspace(agent_name)
    if not file.filename:
        raise HTTPException(400, "未提供文件")

    content = await file.read()
    max_size = 50 * 1024 * 1024
    if len(content) > max_size:
        raise HTTPException(400, "文件过大（最大 50MB）")

    upload_dir = CBHCLI_DIR / "web_uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)
    safe_name = file.filename.replace("/", "_").replace("\\", "_")
    temp_path = upload_dir / safe_name
    temp_path.write_bytes(content)

    kb = _get_kb(agent_name)
    result = kb.add_file(str(temp_path))

    try:
        temp_path.unlink()
    except Exception:
        pass

    if not result.get("success"):
        raise HTTPException(400, result.get("message", "添加失败"))
    return result


@app.delete("/api/agents/{agent_name}/knowledge/{file_name}")
def remove_knowledge_file(agent_name: str, file_name: str):
    kb = _get_kb(agent_name)
    result = kb.remove_file(file_name)
    if not result.get("success"):
        raise HTTPException(404, result.get("message", "文件不存在"))
    return result


@app.post("/api/agents/{agent_name}/knowledge/reindex")
def reindex_knowledge(agent_name: str):
    kb = _get_kb(agent_name)
    return kb.reindex_all()


# ===================================================================
#  API: Embedding / Vector Index
# ===================================================================

@app.get("/api/agents/{agent_name}/embedding/status")
def embedding_status(agent_name: str):
    _init_vector_store()
    _get_agent_workspace(agent_name)
    if not _vector_store:
        return {"enabled": False, "count": 0,
                "message": "向量数据库未启用，请先配置嵌入模型"}
    try:
        count = _vector_store.count(agent_name)
    except Exception:
        count = 0
    return {"enabled": True, "count": count}


@app.post("/api/agents/{agent_name}/embedding/index")
def embedding_index(agent_name: str):
    _init_vector_store()
    config = _get_agent_config(agent_name)
    if not config:
        raise HTTPException(404, f"Agent '{agent_name}' 不存在")
    if not _memory_indexer:
        raise HTTPException(400, "向量数据库未启用，请先配置嵌入模型")

    try:
        _vector_store.delete_collection(agent_name)
        segments = _memory_indexer.index_agent_workspace(agent_name, config.workspace_path)
        if segments > 0:
            return {"message": f"已索引 {segments} 个段落", "segments": segments}
        return {"message": "未找到可索引的内容", "segments": 0}
    except Exception as e:
        raise HTTPException(500, f"索引失败: {e}")


@app.post("/api/agents/{agent_name}/embedding/clear")
def embedding_clear(agent_name: str):
    _init_vector_store()
    _get_agent_workspace(agent_name)
    if not _vector_store:
        raise HTTPException(400, "向量数据库未启用")
    try:
        _vector_store.delete_collection(agent_name)
        return {"message": f"已清除 Agent '{agent_name}' 的索引"}
    except Exception as e:
        raise HTTPException(500, f"清除失败: {e}")


@app.post("/api/agents/{agent_name}/embedding/reindex")
def embedding_reindex(agent_name: str):
    embedding_clear(agent_name)
    return embedding_index(agent_name)


# ===================================================================
#  API: Session History
# ===================================================================

@app.get("/api/agents/{agent_name}/history")
def list_history(agent_name: str, limit: int = 50):
    config = _get_agent_config(agent_name)
    if not config:
        raise HTTPException(404, f"Agent '{agent_name}' 不存在")
    history_mgr = SessionHistoryManager(config.workspace_path)
    return {"sessions": history_mgr.list_sessions(limit)}


@app.get("/api/agents/{agent_name}/history/{filename}")
def get_history(agent_name: str, filename: str):
    config = _get_agent_config(agent_name)
    if not config:
        raise HTTPException(404, f"Agent '{agent_name}' 不存在")
    history_mgr = SessionHistoryManager(config.workspace_path)
    messages = history_mgr.load_session(filename)
    if messages is None:
        raise HTTPException(404, "会话不存在")
    return {"messages": messages}


@app.delete("/api/agents/{agent_name}/history/{filename}")
def delete_history(agent_name: str, filename: str):
    config = _get_agent_config(agent_name)
    if not config:
        raise HTTPException(404, f"Agent '{agent_name}' 不存在")
    history_mgr = SessionHistoryManager(config.workspace_path)
    if history_mgr.delete_session(filename):
        return {"message": "会话已删除"}
    raise HTTPException(404, "会话不存在")


# ===================================================================
#  API: Agent Chains (链条管理)
# ===================================================================

def _get_chain_manager():
    """获取或创建 ChainManager 单例"""
    from cbhcli_pkg.core.agent_chain import ChainManager
    global _chain_manager_instance
    if _chain_manager_instance is None:
        _chain_manager_instance = ChainManager()
    else:
        # 跨进程链条同步：其他进程改动 agent_chains.json 后按 mtime 刷新（v5.2.2）
        try:
            _chain_manager_instance.reload_if_changed()
        except Exception:
            pass
    return _chain_manager_instance

_chain_manager_instance = None


@app.get("/api/chains")
def list_chains():
    cm = _get_chain_manager()
    manager = get_agent_manager()
    chains = []
    for chain in cm.list_chains():
        d = chain.to_dict()
        # 附加每个 Agent 的 description（实时读取）
        for level in d.get("levels", []):
            for agent in level.get("agents", []):
                cfg = manager.load_agent(agent["name"])
                agent["description"] = cfg.description if cfg else ""
                model = cfg.primary_model if cfg else ""
                agent["model"] = model or ""
        # 校验
        missing = chain.validate(manager)
        d["valid"] = len(missing) == 0
        d["missing_agents"] = missing
        chains.append(d)
    return {"chains": chains}


@app.post("/api/chains")
async def create_chain(req: Request):
    data = json.loads(await req.body())
    from cbhcli_pkg.core.agent_chain import AgentChain, ChainLevel, ChainAgent
    cm = _get_chain_manager()
    name = data.get("name", "")
    if not name:
        raise HTTPException(400, "链条名称不能为空")
    if cm.get_chain(name):
        raise HTTPException(400, f"链条 '{name}' 已存在")
    chain = AgentChain(
        name=name,
        description=data.get("description", ""),
        levels=[ChainLevel.from_dict(l) for l in data.get("levels", [])],
    )
    # 校验 Agent 存在性
    missing = chain.validate(get_agent_manager())
    if missing:
        raise HTTPException(400, f"链条中引用了不存在的 Agent: {', '.join(missing)}")
    cm.add_chain(chain)
    return {"message": f"链条 '{name}' 已创建", "chain": chain.to_dict()}


@app.get("/api/chains/{chain_name}")
def get_chain(chain_name: str):
    cm = _get_chain_manager()
    chain = cm.get_chain(chain_name)
    if not chain:
        raise HTTPException(404, f"链条 '{chain_name}' 不存在")
    manager = get_agent_manager()
    d = chain.to_dict()
    for level in d.get("levels", []):
        for agent in level.get("agents", []):
            cfg = manager.load_agent(agent["name"])
            agent["description"] = cfg.description if cfg else ""
            agent["model"] = cfg.primary_model if cfg else ""
    missing = chain.validate(manager)
    d["valid"] = len(missing) == 0
    d["missing_agents"] = missing
    return d


@app.put("/api/chains/{chain_name}")
async def update_chain(chain_name: str, req: Request):
    cm = _get_chain_manager()
    chain = cm.get_chain(chain_name)
    if not chain:
        raise HTTPException(404, f"链条 '{chain_name}' 不存在")
    data = json.loads(await req.body())
    from cbhcli_pkg.core.agent_chain import AgentChain, ChainLevel
    if "description" in data:
        chain.description = data["description"]
    if "levels" in data:
        chain.levels = [ChainLevel.from_dict(l) for l in data["levels"]]
    missing = chain.validate(get_agent_manager())
    if missing:
        raise HTTPException(400, f"链条中引用了不存在的 Agent: {', '.join(missing)}")
    cm.update_chain(chain_name, chain)
    return {"message": f"链条 '{chain_name}' 已更新"}


@app.delete("/api/chains/{chain_name}")
def delete_chain(chain_name: str):
    cm = _get_chain_manager()
    if not cm.get_chain(chain_name):
        raise HTTPException(404, f"链条 '{chain_name}' 不存在")
    cm.remove_chain(chain_name)
    return {"message": f"链条 '{chain_name}' 已删除"}


@app.post("/api/chat/use-chain")
async def use_chain(req: Request):
    """当前会话绑定链条"""
    data = json.loads(await req.body())
    chain_name = data.get("chain_name", "")
    agent_name = data.get("agent_name", "")
    model_name = data.get("model_name", "")

    cm = _get_chain_manager()
    chain = cm.get_chain(chain_name)
    if not chain:
        raise HTTPException(404, f"链条 '{chain_name}' 不存在")

    missing = chain.validate(get_agent_manager())
    if missing:
        raise HTTPException(400, f"链条中引用了不存在的 Agent: {', '.join(missing)}")

    # 校验：只有元 Agent 才能激活对应链条（与 CLI 一致）
    root = chain.get_root_agent()
    if agent_name != root:
        raise HTTPException(400,
            f"链条 '{chain_name}' 的元 Agent 是 '{root}'，"
            f"当前 Agent 是 '{agent_name}'。请先切换到元 Agent 再激活。")

    cs = _resolve_session(agent_name, model_name, data.get("session_id", ""))
    cs.active_chain = chain
    cs.chain_active_path = [chain.get_root_agent()]
    cs.app_proxy.active_chain = chain
    cs.app_proxy.chain_active_path = cs.chain_active_path

    # 持久化激活状态（与 CLI 一致，刷新页面/重新进入 Agent 时自动恢复）
    root = chain.get_root_agent()
    if root:
        get_config().set_active_chain(root, chain_name)

    # 重建工具（注册 call_agent）
    cs._rebuild_tools()
    # 重建系统提示（注入链条信息）
    cs._rebuild_system_prompt()

    return {
        "message": f"链条 '{chain_name}' 已激活",
        "chain_name": chain_name,
        "root_agent": chain.get_root_agent(),
    }


@app.post("/api/chat/off-chain")
async def off_chain(req: Request):
    """取消当前会话的链条绑定"""
    data = json.loads(await req.body())
    agent_name = data.get("agent_name", "")
    model_name = data.get("model_name", "")

    cs = _resolve_session(agent_name, model_name, data.get("session_id", ""))
    cs.active_chain = None
    cs.chain_active_path = None
    cs.app_proxy.active_chain = None
    cs.app_proxy.chain_active_path = None

    # 持久化取消状态
    get_config().set_active_chain(agent_name, None)

    # 重建工具（移除 call_agent）
    cs._rebuild_tools()
    # 重建系统提示
    cs._rebuild_system_prompt()

    return {"message": "链条绑定已取消"}


# ===================================================================
#  API: Chat (SSE Streaming + ReAct Loop)
# ===================================================================

def _sse(data: dict) -> str:
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


# ===================================================================
#  实时事件总线 + 后台运行（v5.2.9：WebSocket 多浏览器同步）
#
#  会话的 ReAct 循环不再绑定 SSE 连接，而是在后台 asyncio 任务中运行：
#  - 浏览器新建/切换会话不影响正在运行的任务（后台继续执行）
#  - 全部事件（流式内容/工具确认/进度）发布到会话事件总线，
#    任意数量的浏览器通过 /ws 订阅同一会话获得一致的实时画面
#  - 事件日志按会话内单调 seq 记录，迟到订阅者（新开的浏览器、
#    断线重连）按 since_seq 回放，不丢事件也不重复
# ===================================================================

def _parse_sse_line(sse_str: str) -> Optional[dict]:
    """把 _react_loop 产出的 SSE 行还原为事件 dict。"""
    s = (sse_str or "").strip()
    if s.startswith("data: "):
        s = s[6:]
    try:
        data = json.loads(s)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _publish_event(cs: 'WebChatSession', data: dict) -> None:
    """发布事件：写入会话事件日志 + 投递给所有订阅者队列。"""
    if not isinstance(data, dict) or cs is None:
        return
    try:
        cs.run_seq += 1
        entry = dict(data)
        entry["seq"] = cs.run_seq
        cs.run_events.append(entry)
        if len(cs.run_events) > _MAX_RUN_EVENTS:
            drop = len(cs.run_events) - _MAX_RUN_EVENTS
            del cs.run_events[:drop]
            cs.run_min_seq = cs.run_events[0]["seq"] - 1
        for q in list(cs.subscribers):
            try:
                q.put_nowait(entry)
            except Exception:
                pass
    except Exception:
        pass


def _session_title(cs: 'WebChatSession') -> str:
    """会话显示标题（自定义标题 > 首条用户消息 > "新会话"）。"""
    title = getattr(cs, "custom_title", "") or ""
    if title:
        return title
    if cs.session:
        for m in cs.session.messages:
            if m.role == "user" and (m.content or "").strip():
                return " ".join(m.content.split())[:50]
    return "新会话"


def _session_status_event(cs: 'WebChatSession', status: str) -> dict:
    """会话状态变化通知（广播给所有 WebSocket 客户端，驱动侧边栏徽标）。"""
    return {
        "type": "session_status",
        "session_id": cs.session.id if cs.session else "",
        "agent": cs.agent_name,
        "model": cs.model_name,
        "status": status,           # running / idle
        "title": _session_title(cs),
    }


def _broadcast_global(payload: dict) -> None:
    """向所有 WebSocket 客户端广播通知（会话状态变化等）。"""
    for client in list(_ws_clients):
        try:
            client.notify(payload)
        except Exception:
            pass


def _live_export_limit(cs: 'WebChatSession') -> Optional[int]:
    """会话运行中导出消息时的截断下标（当前运行的消息由事件回放渲染）。"""
    if cs is not None and getattr(cs, "run_active", False):
        # v5.2.9 修复：第一轮运行时 run_start_msg_count=0，旧写法
        # `n or None` 把"截断到 0 条"错误变成"不截断"——全量导出含本轮
        # user 消息，与 WS 回放的 run_start 事件双份渲染。0 也是有效截断。
        return max(0, int(getattr(cs, "run_start_msg_count", 0) or 0))
    return None


def _ensure_session_cwd(cs: 'WebChatSession') -> None:
    """工具执行前对齐进程 cwd 到会话所属工作空间（v5.2.9 多会话并发）。

    工作空间切换是进程级 chdir，后台运行的会话若不对齐会在错误的
    目录下执行读写操作。注意：多会话"同时"执行工具仍存在极小的
    chdir 竞窗（毫秒级），对齐已覆盖绝大多数场景。
    """
    try:
        ws = getattr(cs, "workspace", "") or ""
        if ws and os.getcwd() != ws:
            os.chdir(ws)
    except Exception:
        pass


def _start_background_run(cs: 'WebChatSession', user_message: str) -> None:
    """启动后台 ReAct 运行任务（若已有任务在跑则返回，不重复启动）。"""
    if cs.run_task is not None and not cs.run_task.done():
        return
    import time as _time
    cs.run_active = True
    cs.run_events = []
    cs.run_min_seq = 0
    cs.respond_waiting = False
    cs.last_active_ts = _time.time()
    # 本次运行首条用户消息是最后一条消息：导出历史时截断到它之前，
    # 该消息及后续由事件日志回放渲染（保证多浏览器一致）
    cs.run_start_msg_count = max(0, len(cs.session.messages) - 1)
    _publish_event(cs, {
        "type": "run_start",
        "session_id": cs.session.id,
        "message": user_message,
        "agent": cs.agent_name,
        "model": cs.model_name,
    })
    # SessionStart 钩子输出（会话创建时收集，首次聊天时随事件下发）
    if cs.hook_start_outputs:
        for line in list(cs.hook_start_outputs):
            _publish_event(cs, {"type": "hook_output",
                                "event": "SessionStart", "content": line})
        cs.hook_start_outputs = []
    _broadcast_global(_session_status_event(cs, "running"))
    cs.run_task = asyncio.create_task(_background_react_runner(cs))


async def _background_react_runner(cs: 'WebChatSession'):
    """后台执行 ReAct 循环：持有会话锁、逐事件发布到总线。

    与 SSE 连接完全解耦--浏览器断开/切换会话/关闭页面均不影响执行。
    """
    try:
        async with cs.lock:
            async for ev in _react_loop(cs):
                data = _parse_sse_line(ev)
                if data is not None:
                    _publish_event(cs, data)
            # Stop 钩子：AI 回复完成后触发（与 CLI 一致）
            if cs.hook_manager and cs.hook_manager.has_hooks("Stop"):
                try:
                    decision = await asyncio.to_thread(
                        cs.hook_manager.run_simple, "Stop",
                        session_id=cs.session.id)
                    for line in decision.outputs:
                        _publish_event(cs, {"type": "hook_output",
                                            "event": "Stop", "content": line})
                except Exception:
                    pass
    except asyncio.CancelledError:
        _publish_event(cs, {"type": "aborted"})
    except Exception as e:
        _publish_event(cs, {"type": "error", "content": str(e)})
    finally:
        import time as _time
        cs.run_active = False
        cs.respond_waiting = False
        cs.last_active_ts = _time.time()
        try:
            _publish_event(cs, {"type": "run_end", "usage": cs.usage_stats()})
        except Exception:
            _publish_event(cs, {"type": "run_end"})
        _broadcast_global(_session_status_event(cs, "idle"))
        if cs.run_task is not None and cs.run_task.done():
            cs.run_task = None


class _WsClient:
    """单个 WebSocket 连接：统一发送队列 + 至多一个会话的事件订阅。

    所有出站消息（订阅回执/事件回放/全局通知）进入单一 out 队列，
    由唯一 sender 任务串行写出，避免并发 send_text 交错。
    """

    def __init__(self, ws: WebSocket):
        self.ws = ws
        self.out: asyncio.Queue = asyncio.Queue()
        self.session_id: Optional[str] = None
        self.cs: Optional['WebChatSession'] = None
        self.queue: Optional[asyncio.Queue] = None
        self._pump_task: Optional[asyncio.Task] = None
        self._sender_task: Optional[asyncio.Task] = None
        self._closed = False

    # ---- 出站 ----
    def _put(self, payload: dict) -> None:
        if self._closed:
            return
        if self.out.qsize() > 5000:
            # 慢消费者保护：积压过多直接断开，客户端重连后按 seq 回放恢复
            self._closed = True
            asyncio.ensure_future(self._close())
            return
        self.out.put_nowait(payload)

    def notify(self, payload: dict) -> None:
        """全局通知（任意任务调用，非阻塞）。"""
        self._put({"type": "notice", "data": payload})

    async def _sender(self) -> None:
        while True:
            payload = await self.out.get()
            await self.ws.send_text(json.dumps(payload, ensure_ascii=False))

    async def _close(self) -> None:
        try:
            await self.ws.close()
        except Exception:
            pass

    # ---- 订阅管理 ----
    def subscribe(self, session_id: str, since_seq: int) -> None:
        self.unsubscribe()
        cs = _sessions_by_id.get(session_id)
        if cs is None:
            self._put({"type": "subscribed", "session_id": session_id,
                       "error": "会话不存在"})
            return
        # 快照与订阅之间无 await：asyncio 单线程下原子，事件不丢不重
        snapshot = [ev for ev in cs.run_events if ev.get("seq", 0) > since_seq]
        q: asyncio.Queue = asyncio.Queue()
        cs.subscribers.append(q)
        self.session_id = session_id
        self.cs = cs
        self.queue = q
        need_resync = since_seq < cs.run_min_seq
        self._put({"type": "subscribed", "session_id": session_id,
                   "seq": cs.run_seq, "run_active": cs.run_active,
                   "resync": need_resync})
        if not need_resync:
            for ev in snapshot:
                self._put({"type": "event", "data": ev})

        async def _pump():
            try:
                while self.queue is q:
                    ev = await q.get()
                    self._put({"type": "event", "data": ev})
            except asyncio.CancelledError:
                pass

        self._pump_task = asyncio.create_task(_pump())

    def unsubscribe(self) -> None:
        if self.cs is not None and self.queue is not None:
            try:
                self.cs.subscribers.remove(self.queue)
            except ValueError:
                pass
        self.session_id = None
        self.cs = None
        self.queue = None
        if self._pump_task is not None:
            self._pump_task.cancel()
            self._pump_task = None

    async def start(self) -> None:
        self._sender_task = asyncio.create_task(self._sender())

    async def stop(self) -> None:
        self._closed = True
        self.unsubscribe()
        if self._sender_task is not None:
            self._sender_task.cancel()
            self._sender_task = None
        await self._close()


@app.websocket("/ws")
async def ws_endpoint(websocket: WebSocket):
    """WebSocket 实时通道（v5.2.9）。

    客户端 -> 服务端:
      {"type": "subscribe", "session_id": "...", "since_seq": N}
      {"type": "unsubscribe"}
      {"type": "ping"}
    服务端 -> 客户端:
      {"type": "subscribed", seq, run_active, resync}   订阅回执（含回放判断）
      {"type": "event", data: {...事件, seq}}           会话事件（回放+实时）
      {"type": "notice", data: {...}}                   全局通知（会话状态等）
      {"type": "pong"}
    """
    await websocket.accept()
    client = _WsClient(websocket)
    _ws_clients.add(client)
    try:
        await client.start()
        while True:
            raw = await websocket.receive_text()
            try:
                msg = json.loads(raw)
            except Exception:
                continue
            mtype = msg.get("type", "")
            if mtype == "subscribe":
                client.subscribe(str(msg.get("session_id", "")),
                                 int(msg.get("since_seq", 0) or 0))
            elif mtype == "unsubscribe":
                client.unsubscribe()
            elif mtype == "ping":
                client._put({"type": "pong"})
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        try:
            await client.stop()
        except Exception:
            pass
        _ws_clients.discard(client)


def _tool_preview(tool_name: str, arguments: dict) -> str:
    """工具参数摘要，用于确认卡片展示。"""
    if tool_name == "terminal":
        return (arguments.get("command", "") or "")[:200]
    if tool_name in ("read", "write", "edit"):
        return arguments.get("file_path", arguments.get("path", ""))
    if tool_name == "grep":
        return f"/{arguments.get('pattern', '')}/ in {arguments.get('path', '.')}"
    if tool_name == "glob":
        return arguments.get("pattern", "")
    if tool_name == "ask_user":
        return arguments.get("question", "")[:100]
    if tool_name == "python":
        return (arguments.get("code", "") or "")[:200]
    if tool_name == "delegate_task":
        task = arguments.get("task", "")
        if task:
            return task[:120]
        tasks = arguments.get("tasks", [])
        return f"并行委托 {len(tasks)} 个子任务"
    if tool_name == "skills_create":
        return arguments.get("skill_name", "")
    if tool_name == "image":
        paths = arguments.get("image_paths", [])
        return f"{len(paths)} 张图片"
    return json.dumps(arguments, ensure_ascii=False)[:150]


async def _stream_round(cs: WebChatSession, messages: list, stream_kwargs: dict,
                        result: dict):
    """单轮流式请求（含备用模型切换）。产生 SSE 事件并填充 result。

    result 键: ai_response, reasoning, tool_calls, error, aborted
    """
    config = get_config()
    fallback_names = config.get_fallback_models()
    active_client = cs.llm_client
    fallback_tried: set[str] = set()
    loop = asyncio.get_event_loop()

    while True:
        ai_response = ""
        reasoning_buffer = ""
        tc_buffer: dict[int, dict] = {}
        stream_error = None

        # 文本复读检测（与 CLI 一致：尾部块重复 3 次判定复读，截断继续）
        reasoning_loop = TextLoopDetector()
        content_loop = TextLoopDetector()
        text_loop_stop = threading.Event()

        chunks_queue: asyncio.Queue = asyncio.Queue()

        def stream_worker(client=active_client):
            try:
                stream = client.chat_stream(messages, **stream_kwargs)
                for chunk_type, content in stream:
                    if cs.abort:
                        asyncio.run_coroutine_threadsafe(
                            chunks_queue.put(("aborted", "")), loop)
                        return
                    if text_loop_stop.is_set():
                        # 文本复读熔断：关闭流并通知消费端
                        try:
                            stream.close()
                        except Exception:
                            pass
                        asyncio.run_coroutine_threadsafe(
                            chunks_queue.put(("text_loop", "")), loop)
                        return
                    asyncio.run_coroutine_threadsafe(
                        chunks_queue.put((chunk_type, content)), loop)
            except Exception as e:
                asyncio.run_coroutine_threadsafe(
                    chunks_queue.put(("error", str(e))), loop)
            finally:
                asyncio.run_coroutine_threadsafe(chunks_queue.put(None), loop)

        threading.Thread(target=stream_worker, daemon=True).start()

        # 消费流（轮询以快速响应中断）
        stream_done = False
        while not stream_done:
            if cs.abort:
                result["aborted"] = True
                return
            try:
                item = await asyncio.wait_for(chunks_queue.get(), timeout=0.5)
            except asyncio.TimeoutError:
                continue

            if item is None:
                stream_done = True
                break
            chunk_type, content = item

            if chunk_type == "error":
                stream_error = content
                stream_done = True
            elif chunk_type == "aborted":
                result["aborted"] = True
                return
            elif chunk_type == "text_loop":
                # 文本复读熔断：以当前已收到内容截断结束本轮
                result["text_loop"] = True
                if content_loop.triggered:
                    ai_response = content_loop.truncated_text()
                yield _sse({"type": "loop_detected", "verdict": "text_loop",
                            "tool_name": "", "tool_id": ""})
                stream_done = True
            elif chunk_type == "reasoning":
                reasoning_buffer += content
                if reasoning_loop.feed(content):
                    text_loop_stop.set()
                else:
                    yield _sse({"type": "reasoning", "content": content})
            elif chunk_type == "content":
                ai_response += content
                if content_loop.feed(content):
                    text_loop_stop.set()
                else:
                    yield _sse({"type": "content", "content": content})
            elif chunk_type == "tool_calls":
                try:
                    for tc in json.loads(content):
                        idx = tc.get("index", 0)
                        if idx not in tc_buffer:
                            tc_buffer[idx] = {"id": "", "name": "", "arguments": ""}
                        if tc.get("id"):
                            tc_buffer[idx]["id"] = tc["id"]
                        func = tc.get("function", {})
                        if func.get("name"):
                            tc_buffer[idx]["name"] = func["name"]
                        if "arguments" in func:
                            tc_buffer[idx]["arguments"] += func["arguments"]
                except json.JSONDecodeError:
                    pass

        if stream_error is None:
            # 成功 — 组装工具调用
            tool_calls = []
            for idx in sorted(tc_buffer.keys()):
                tc = tc_buffer[idx]
                if not tc["name"]:
                    continue
                tc_id = tc["id"] or f"call_{uuid.uuid4().hex[:8]}"
                try:
                    args = json.loads(tc["arguments"]) if tc["arguments"] else {}
                    args = _fix_unicode_escapes(args)
                except json.JSONDecodeError:
                    args = {}
                tool_calls.append({"id": tc_id, "name": tc["name"], "arguments": args})
            result.update({
                "ai_response": ai_response,
                "reasoning": reasoning_buffer,
                "tool_calls": tool_calls,
                "error": None,
            })
            return

        # 失败 — 尝试备用模型
        switched = False
        for fb_name in fallback_names:
            if fb_name in fallback_tried:
                continue
            fb_config = config.get_model(fb_name)
            if not fb_config:
                continue
            fallback_tried.add(fb_name)
            # v5.2.8：携带详细报错信息（与 CLI 一致，不再只显示"调用失败"）
            yield _sse({"type": "fallback",
                        "content": f"主模型调用失败: {stream_error}，"
                                   f"切换到备用模型 '{fb_name}'..."})
            active_client = LLMClient(fb_config)
            switched = True
            break

        if not switched:
            result.update({
                "ai_response": ai_response,
                "reasoning": reasoning_buffer,
                "tool_calls": [],
                "error": stream_error,
            })
            return


def _fill_aborted_tool_msgs(cs: WebChatSession, valid_calls: list):
    """abort 中断时为未执行的 tool_call 补 tool 消息，保持 OAI 消息序列完整。

    assistant(tool_calls) 消息已记录但工具未全部执行时，
    若不补全 tool 消息，下一次请求会报 400：
    "An assistant message with 'tool_calls' must be followed by tool messages..."
    """
    executed_ids = {
        m.tool_call_id for m in cs.session.messages if m.role == "tool"
    }
    for tc in valid_calls:
        if tc["id"] in executed_ids:
            continue
        cs.session.add_message(
            "tool",
            "[系统补全] 工具执行被用户中断，未产生结果。请根据上下文继续任务。",
            metadata={"tool_name": tc["tool"], "success": False},
            tool_call_id=tc["id"])


async def _execute_tool_interruptible(cs: 'WebChatSession', tool_name: str,
                                      tool_args: dict) -> ToolResult:
    """线程执行工具，期间轮询 abort 标志；用户中断时触发工具的 interrupt()
    （terminal 会立即杀掉子进程）——修复"工具运行中无法中断"（v5.2.9）。

    无 interrupt() 方法的工具（python/read/write 等）等待其自然结束
    （通常为快速操作；python 代码执行无法安全杀线程）。
    """
    loop = asyncio.get_event_loop()
    future = loop.run_in_executor(
        None, lambda: cs.tool_registry.execute(tool_name, **tool_args))
    interrupted = False
    while not future.done():
        if cs.abort and not interrupted:
            interrupted = True
            try:
                tool = cs.tool_registry.get(tool_name)
            except Exception:
                tool = None
            if tool is not None and hasattr(tool, "interrupt"):
                try:
                    tool.interrupt()
                except Exception:
                    pass
        await asyncio.sleep(0.3)
    return await future  # 异常由调用方捕获


def _autosave_web_session(cs: "WebChatSession") -> None:
    """每轮对话结束自动保存会话到 history（v5.2.6）

    覆盖 _react_loop 全部出口：done / aborted / error / 死循环熔断 /
    达到最大轮数 / SSE 连接断开（客户端 aclose 生成器触发 finally）。
    同 session_id 幂等覆盖同一文件，Web/Jupyter 服务重启或崩溃时
    最多丢失正在生成的当前轮，已完成的对话轮均已落盘。
    """
    try:
        if cs and cs.session and len(cs.session.messages) > 1:
            cfg = cs.agent_config or _get_agent_config(getattr(cs, "agent_name", ""))
            if cfg:
                SessionHistoryManager(cfg.workspace_path).save_session(
                    cs.session.get_context_messages(), cs.session.id,
                    workspace=getattr(cs, "workspace", "") or "",
                    title=getattr(cs, "custom_title", "") or "")
    except Exception:
        pass  # 保存失败不影响对话


async def _react_loop(cs: WebChatSession):
    """完整 ReAct 循环（与 CLI ai_handler 对齐），产生 SSE 事件。

    v5.2.6：wrapper 层 try/finally 保证每轮对话结束（含全部出口与
    SSE 连接断开）自动保存会话到 history，实际循环在 _react_loop_inner。
    """
    try:
        async for ev in _react_loop_inner(cs):
            yield ev
    finally:
        _autosave_web_session(cs)


async def _react_loop_inner(cs: WebChatSession):
    """（v5.2.6 由 _react_loop 包装）完整 ReAct 循环，产生 SSE 事件。"""
    failure_counts: dict[str, int] = {}
    openai_tools = cs.tool_registry.get_openai_tools()
    stream_kwargs = {"temperature": API_TEMPERATURE}
    if openai_tools:
        stream_kwargs["tools"] = openai_tools
        stream_kwargs["tool_choice"] = "auto"

    # Harness：死循环检测器（每个用户请求独立）+ 权限引擎
    loop_tracker = ToolCallTracker()
    loop_aborted = False
    # v5.2.3：压缩失败冷却——失败后本请求内不再重试，避免系统性失败导致
    # 每轮 ReAct 循环都浪费一次摘要 API 调用（最坏卡满 API_TIMEOUT）
    compress_failed = False
    engine = cs.permission_engine or get_permission_engine()

    for round_idx in range(MAX_TOOL_ROUNDS):
        if cs.abort:
            yield _sse({"type": "aborted"})
            return

        # ---- ReAct 循环内自动压缩 ----
        if (cs.auto_compress and cs.context_compressor and cs.context_window
                and not compress_failed):
            total_tokens = cs.session.get_total_tokens(cs.token_counter)
            cs.context_window.update(total_tokens)
            if cs.context_window.needs_compression():
                yield _sse({"type": "compressing",
                            "content": f"上下文接近上限 ({cs.context_window.get_status_text()})，正在自动压缩..."})
                try:
                    success = await asyncio.to_thread(
                        cs.context_compressor.compress,
                        cs.session, cs.context_window.compression_target())
                except Exception:
                    success = False
                if success:
                    new_tokens = cs.session.get_total_tokens(cs.token_counter)
                    cs.context_window.update(new_tokens)
                    yield _sse({"type": "compressed",
                                "content": f"上下文已压缩 ({cs.context_window.get_status_text()})"})
                else:
                    compress_failed = True
                    err = getattr(cs.context_compressor, "last_error", None)
                    msg = (f"压缩失败: {err}，继续执行（本次请求内不再重试）" if err
                           else "压缩失败，继续执行（本次请求内不再重试）")
                    yield _sse({"type": "compress_failed", "content": msg})

        messages = cs.session.get_context_messages()
        # 防御性修复：补全缺失 tool 消息 / 移动插队的 user 消息
        # （DeepSeek 等 API 严格要求 assistant(tool_calls) 后紧跟全部 tool 消息）
        messages = repair_tool_messages(messages)

        # ---- 流式请求一轮 ----
        result: dict = {}
        async for ev in _stream_round(cs, messages, stream_kwargs, result):
            yield ev

        if result.get("aborted"):
            yield _sse({"type": "aborted"})
            return

        if result.get("error"):
            yield _sse({"type": "error", "content": result["error"]})
            cs.session.add_message(
                "assistant", result.get("ai_response") or "Error",
                reasoning_content=result.get("reasoning") or None)
            return

        ai_response = result.get("ai_response", "")
        reasoning_buffer = result.get("reasoning", "")
        tool_calls = result.get("tool_calls", [])

        # 文本复读截断：若本轮还有工具调用，提示附在回复里让模型下轮看到
        if result.get("text_loop") and tool_calls:
            ai_response = (ai_response or "") + (
                "\n\n[系统提示] 上一条输出陷入重复循环，已被系统截断。"
                "请避免重复内容，简明扼要地继续任务。")
            if cs.tracer:
                cs.tracer.log_loop("text_loop", detail="流式输出复读截断")

        # ---- 无工具调用 → 结束 ----
        if not tool_calls:
            if ai_response:
                cs.session.add_message(
                    "assistant", ai_response,
                    reasoning_content=reasoning_buffer or None)
            yield _sse({"type": "done", "usage": cs.usage_stats()})
            return

        # ---- 去重 + 解析工具名（与 CLI 一致）----
        valid_calls = []
        seen = set()
        for tc in tool_calls:
            tool_obj = cs.tool_registry.fuzzy_get(tc["name"])
            if not tool_obj:
                continue
            resolved = tool_obj.name
            args_key = json.dumps(tc.get("arguments", {}), sort_keys=True)
            if (resolved, args_key) in seen:
                continue
            seen.add((resolved, args_key))
            valid_calls.append({
                "id": tc["id"], "tool": resolved,
                "arguments": tc.get("arguments", {}),
            })

        if not valid_calls:
            # 所有工具调用都无法解析（工具名错误/重复），必须向会话添加提示
            # 否则下一轮循环用相同上下文，AI 可能返回相同无效 tool_calls → 无限循环
            # （与 CLI ai_handler._execute_tools 处理模式一致：反馈后继续循环让 AI 自我纠正）
            invalid_names = [tc["name"] for tc in tool_calls]
            error_msg = (
                f"⚠️ 工具调用失败：以下工具名无法识别或重复调用：{', '.join(invalid_names)}\n"
                f"可用工具：{', '.join(cs.tool_registry.get_available_tools())}\n"
                f"请使用正确的工具名重新调用。"
            )
            # 添加 assistant 消息（含 tool_calls）和 tool 错误消息，保持 OAI 消息序列完整
            cs.session.add_message(
                "assistant", ai_response or "",
                reasoning_content=reasoning_buffer or None,
                tool_calls=[{
                    "id": tc["id"], "type": "function",
                    "function": {"name": tc["name"],
                                 "arguments": json.dumps(tc.get("arguments", {}), ensure_ascii=False)},
                } for tc in tool_calls])
            for tc in tool_calls:
                cs.session.add_message(
                    "tool", error_msg,
                    metadata={"tool_name": tc["name"], "success": False},
                    tool_call_id=tc["id"])
            yield _sse({"type": "error", "content": error_msg})
            continue

        # ---- 记录 assistant 消息（含 tool_calls）----
        openai_tool_calls = [{
            "id": tc["id"], "type": "function",
            "function": {"name": tc["tool"],
                         "arguments": json.dumps(tc["arguments"], ensure_ascii=False)},
        } for tc in valid_calls]
        cs.session.add_message(
            "assistant", ai_response or "",
            reasoning_content=reasoning_buffer or None,
            tool_calls=openai_tool_calls)

        # ---- 逐个执行工具 ----
        pending_image_msgs = []  # 图片 user 消息延迟到所有 tool 消息之后统一追加
        for tc in valid_calls:
            if cs.abort:
                # 补全未执行的 tool 消息，保持消息序列完整
                _fill_aborted_tool_msgs(cs, valid_calls)
                yield _sse({"type": "aborted"})
                return

            # v5.2.9：工具执行前对齐进程 cwd 到会话所属工作空间
            # （多会话并发时，后台会话不会被其他会话的工作空间切换带偏）
            _ensure_session_cwd(cs)

            tool_name = tc["tool"]
            tool_args = tc["arguments"]
            tool_id = tc["id"]

            # ---- ask_user 特判（web 交互）----
            if tool_name == "ask_user":
                yield _sse({
                    "type": "ask_user",
                    "question": tool_args.get("question", "AI 需要你的输入"),
                    "options": tool_args.get("options", []),
                    "allow_multiple": tool_args.get("allow_multiple", False),
                    "tool_id": tool_id,
                })
                answer = await cs.wait_response(timeout=600)
                if answer is None:
                    if cs.abort:
                        # 补全未执行的 tool 消息，保持消息序列完整
                        _fill_aborted_tool_msgs(cs, valid_calls)
                        yield _sse({"type": "aborted"})
                        return
                    answer = "用户未回答"
                tool_output = f"用户回答: {answer}"
                cs.session.add_message(
                    "tool", tool_output, tool_call_id=tool_id,
                    metadata={"tool_name": tool_name, "success": True})
                yield _sse({
                    "type": "tool_result", "tool_name": tool_name,
                    "tool_id": tool_id, "success": True,
                    "preview": tool_output,
                    "answer": answer,
                })
                continue

            # ---- 死循环检测（同参数重复 / 周期震荡）----
            verdict, loop_msg = loop_tracker.check(tool_name, tool_args)
            if verdict != "ok":
                yield _sse({"type": "loop_detected", "verdict": verdict,
                            "tool_name": tool_name, "tool_id": tool_id})
                if cs.tracer:
                    cs.tracer.log_loop(verdict, tool_name)

            if verdict == "abort":
                loop_aborted = True
                cs.session.add_message(
                    "tool", "🛑 [系统熔断] 多次陷入死循环，本轮任务已终止。",
                    tool_call_id=tool_id,
                    metadata={"tool_name": tool_name, "success": False})
                yield _sse({
                    "type": "tool_result", "tool_name": tool_name,
                    "tool_id": tool_id, "success": False,
                    "preview": "🛑 死循环熔断：本轮任务已终止"})
                break

            if verdict == "block":
                cs.session.add_message(
                    "tool", loop_msg, tool_call_id=tool_id,
                    metadata={"tool_name": tool_name, "success": False})
                yield _sse({
                    "type": "tool_result", "tool_name": tool_name,
                    "tool_id": tool_id, "success": False,
                    "preview": "🛑 死循环熔断：重复调用已被阻止，已告知模型换策略"})
                continue

            # ---- 权限规则引擎（Harness 治理层）----
            action, rule = engine.check(tool_name, tool_args)

            if action == PERM_DENY:
                reason = (f"操作被权限规则禁止: {rule}"
                          f"（当前权限模式: {engine.mode}）")
                yield _sse({
                    "type": "tool_denied", "tool_name": tool_name,
                    "tool_id": tool_id, "rule": str(rule), "reason": reason})
                if cs.tracer:
                    cs.tracer.log_tool_blocked(
                        tool_name, tool_args, str(rule), "permission")
                cs.session.add_message(
                    "tool",
                    f"{reason}。请改用其他方式完成任务，"
                    f"或请用户切换权限模式/调整规则。",
                    tool_call_id=tool_id,
                    metadata={"tool_name": tool_name, "success": False})
                continue

            # ---- PreToolUse 钩子（可拦截）----
            if cs.hook_manager and cs.hook_manager.has_hooks("PreToolUse"):
                decision = await asyncio.to_thread(
                    cs.hook_manager.run_pre_tool_use,
                    tool_name, tool_args, cs.session.id)
                if decision.blocked:
                    reason = f"被 PreToolUse 钩子拦截: {decision.block_reason}"
                    yield _sse({
                        "type": "tool_denied", "tool_name": tool_name,
                        "tool_id": tool_id, "rule": "hook", "reason": reason})
                    if cs.tracer:
                        cs.tracer.log_tool_blocked(
                            tool_name, tool_args, decision.block_reason, "hook")
                    cs.session.add_message(
                        "tool", reason, tool_call_id=tool_id,
                        metadata={"tool_name": tool_name, "success": False})
                    continue

            if action == PERM_WARN:
                yield _sse({
                    "type": "tool_yolo_warn", "tool_name": tool_name,
                    "tool_id": tool_id, "rule": str(rule)})

            # ---- 工具确认（ASK 规则才需要人工确认）----
            needs_confirm = (
                action == PERM_ASK
                and tool_name not in _READONLY_TOOLS
                and not cs.no_more_confirmations
            )
            yield _sse({
                "type": "tool_confirm",
                "tool_name": tool_name,
                "tool_args": tool_args,
                "preview": _tool_preview(tool_name, tool_args),
                "tool_id": tool_id,
                "needs_confirm": needs_confirm,
            })

            if needs_confirm:
                user_response = await cs.wait_response(timeout=600)
                if user_response is None:
                    if cs.abort:
                        yield _sse({"type": "aborted"})
                        return
                    user_response = "n"
                user_response = user_response.strip().lower()
            else:
                user_response = "y"
                yield _sse({"type": "tool_auto_confirmed",
                            "tool_name": tool_name, "tool_id": tool_id})

            if user_response == "all":
                cs.no_more_confirmations = True
                user_response = "y"

            if user_response == "always":
                # 提炼一条 allow 规则永久生效（与 CLI 一致）
                rule_s = PermissionEngine.suggest_allow_rule(tool_name, tool_args)
                engine.add_rule("allow", rule_s)
                yield _sse({"type": "rule_added", "category": "allow",
                            "rule": rule_s})
                user_response = "y"

            if user_response in ("n", "no"):
                yield _sse({"type": "tool_rejected",
                            "tool_name": tool_name, "tool_id": tool_id})
                cs.session.add_message(
                    "tool", "用户取消了执行", tool_call_id=tool_id,
                    metadata={"tool_name": tool_name, "success": False})
                continue

            # ---- 写操作前备份检查点（/undo 回滚）----
            if cs.checkpoint_manager and tool_name in ("write", "edit"):
                fp = tool_args.get("file_path", "")
                if fp:
                    await asyncio.to_thread(
                        cs.checkpoint_manager.backup, fp, tool_name)

            # ---- python 工具：执行前记录图片文件快照（Web 端检测新生成图片）----
            _pre_images = set()
            _img_exts = {'.png', '.jpg', '.jpeg', '.gif', '.svg', '.webp', '.bmp'}
            if tool_name == "python":
                for _dir in (os.getcwd(), '/tmp'):
                    try:
                        for _f in os.listdir(_dir):
                            if os.path.splitext(_f)[1].lower() in _img_exts:
                                _pre_images.add(os.path.join(_dir, _f))
                    except Exception:
                        pass

            # ---- 执行工具（线程池，避免阻塞事件循环）----
            yield _sse({"type": "tool_executing",
                        "tool_name": tool_name, "tool_id": tool_id})
            import time as _time
            _t0 = _time.monotonic()

            if tool_name == "call_agent":
                # 链条下游 Agent 调用：执行期间下游的工具确认/ask_user/输出
                # 通过事件队列路由到 Web UI（SSE）
                import queue as _queue_mod
                import threading as _threading_mod

                _chain_evt_q: "_queue_mod.Queue" = _queue_mod.Queue()
                _chain_confirm_evt = _threading_mod.Event()
                _chain_confirm_resp = [None]   # mutable holder
                _chain_no_more = [False]       # 下游 "all" 免确认标志
                _chain_ask_evt = _threading_mod.Event()
                _chain_ask_resp = [None]       # ask_user 回答 holder

                _target_agent = tool_args.get("agent_name", "")

                # 发送 chain_call_start 事件
                yield _sse({
                    "type": "chain_call_start",
                    "agent_name": _target_agent,
                    "task": tool_args.get("task", "")[:200],
                })

                def _chain_confirm_cb(ct_name, ct_args):
                    """下游 Agent 线程中调用：阻塞等待 Web 用户确认结果。"""
                    if _chain_no_more[0]:
                        return True
                    _chain_evt_q.put({
                        "type": "_chain_confirm",
                        "tool_name": ct_name,
                        "tool_args": ct_args,
                    })
                    _chain_confirm_evt.wait(timeout=600)
                    _chain_confirm_evt.clear()
                    resp = _chain_confirm_resp[0]
                    _chain_confirm_resp[0] = None
                    if resp is None or resp in ("n", "no"):
                        return False
                    if resp == "all":
                        _chain_no_more[0] = True
                    return True

                def _chain_ask_user_cb(question, options, allow_multiple):
                    """下游 Agent 线程中调用：阻塞等待 Web 用户回答 ask_user。"""
                    _chain_evt_q.put({
                        "type": "_chain_ask_user",
                        "question": question,
                        "options": options or [],
                        "allow_multiple": allow_multiple or False,
                    })
                    _chain_ask_evt.wait(timeout=600)
                    _chain_ask_evt.clear()
                    resp = _chain_ask_resp[0]
                    _chain_ask_resp[0] = None
                    return resp if resp else "用户未回答"

                def _chain_event_cb(event_type, agent_name, **data):
                    """下游 Agent 线程中调用：将执行过程事件推入队列。"""
                    _chain_evt_q.put({
                        "type": f"_chain_evt_{event_type}",
                        "agent_name": agent_name,
                        **data,
                    })

                cs.app_proxy._chain_confirm_callback = _chain_confirm_cb
                cs.app_proxy._chain_ask_user_callback = _chain_ask_user_cb
                cs.app_proxy._chain_event_callback = _chain_event_cb

                # 在后台线程执行下游 Agent（其内部确认经上面的回调路由到 SSE）
                _chain_future = asyncio.get_event_loop().run_in_executor(
                    None, lambda: cs.tool_registry.execute(tool_name, **tool_args))

                # 轮询事件直到下游执行完毕
                while not _chain_future.done():
                    try:
                        _cev = _chain_evt_q.get_nowait()
                    except _queue_mod.Empty:
                        await asyncio.sleep(0.1)
                        continue

                    _cev_type = _cev.get("type", "")

                    # ---- 下游 Agent 工具确认请求 ----
                    if _cev_type == "_chain_confirm":
                        _ct_name = _cev["tool_name"]
                        _ct_args = _cev["tool_args"]

                        # 下游工具先过权限引擎（deny 红线直接拒绝不弹确认）
                        _ct_action, _ct_rule = engine.check(_ct_name, _ct_args)
                        if _ct_action == PERM_DENY:
                            _chain_confirm_resp[0] = "n"
                            _chain_confirm_evt.set()
                            yield _sse({
                                "type": "tool_denied",
                                "tool_name": _ct_name,
                                "tool_id": tool_id,
                                "rule": str(_ct_rule),
                                "reason": (f"下游 Agent [{_target_agent}] "
                                           f"操作被权限规则禁止: {_ct_rule}"),
                                "chain_agent": _target_agent,
                            })
                            continue
                        if _ct_action == PERM_WARN:
                            yield _sse({
                                "type": "tool_yolo_warn",
                                "tool_name": _ct_name,
                                "tool_id": tool_id,
                                "rule": str(_ct_rule),
                                "chain_agent": _target_agent,
                            })

                        # 弹出确认框（与主会话确认一致的 UI）
                        yield _sse({
                            "type": "tool_confirm",
                            "tool_name": _ct_name,
                            "tool_args": _ct_args,
                            "preview": _tool_preview(_ct_name, _ct_args),
                            "tool_id": tool_id,
                            "needs_confirm": True,
                            "chain_agent": _target_agent,
                        })
                        _uresp = await cs.wait_response(timeout=600)
                        if _uresp is None:
                            if cs.abort:
                                _chain_confirm_resp[0] = "n"
                                _chain_confirm_evt.set()
                                yield _sse({"type": "aborted"})
                                return
                            _uresp = "n"
                        _uresp = _uresp.strip().lower()

                        if _uresp == "always":
                            _rs = PermissionEngine.suggest_allow_rule(
                                _ct_name, _ct_args)
                            engine.add_rule("allow", _rs)
                            yield _sse({"type": "rule_added",
                                        "category": "allow", "rule": _rs})
                            _uresp = "y"

                        _chain_confirm_resp[0] = _uresp
                        _chain_confirm_evt.set()

                        if _uresp in ("n", "no"):
                            yield _sse({
                                "type": "tool_rejected",
                                "tool_name": _ct_name,
                                "tool_id": tool_id,
                                "chain_agent": _target_agent,
                            })

                    # ---- 下游 Agent ask_user 请求 ----
                    elif _cev_type == "_chain_ask_user":
                        yield _sse({
                            "type": "ask_user",
                            "question": _cev.get("question", ""),
                            "options": _cev.get("options", []),
                            "allow_multiple": _cev.get("allow_multiple", False),
                            "tool_id": tool_id,
                            "chain_agent": _target_agent,
                        })
                        _ans = await cs.wait_response(timeout=600)
                        if _ans is None:
                            if cs.abort:
                                _chain_ask_resp[0] = ""
                                _chain_ask_evt.set()
                                yield _sse({"type": "aborted"})
                                return
                            _ans = "用户未回答"
                        _chain_ask_resp[0] = _ans
                        _chain_ask_evt.set()
                        yield _sse({
                            "type": "chain_call_ask_answered",
                            "agent_name": _target_agent,
                            "answer": _ans,
                        })

                    # ---- 下游 Agent 执行过程事件（content/reasoning/tool） ----
                    elif _cev_type == "_chain_evt_content":
                        yield _sse({
                            "type": "chain_call_content",
                            "agent_name": _cev.get("agent_name", _target_agent),
                            "content": _cev.get("content", ""),
                        })
                    elif _cev_type == "_chain_evt_reasoning":
                        yield _sse({
                            "type": "chain_call_reasoning",
                            "agent_name": _cev.get("agent_name", _target_agent),
                            "content": _cev.get("content", ""),
                        })
                    elif _cev_type == "_chain_evt_tool_call":
                        yield _sse({
                            "type": "chain_call_tool",
                            "agent_name": _cev.get("agent_name", _target_agent),
                            "tool_name": _cev.get("tool_name", ""),
                            "arguments": _cev.get("arguments", {}),
                        })
                    elif _cev_type == "_chain_evt_tool_result":
                        yield _sse({
                            "type": "chain_call_tool_result",
                            "agent_name": _cev.get("agent_name", _target_agent),
                            "tool_name": _cev.get("tool_name", ""),
                            "success": _cev.get("success", False),
                            "output": _cev.get("output", ""),
                            "error": _cev.get("error", ""),
                        })

                # 下游执行完毕，取结果
                try:
                    result = _chain_future.result()
                except Exception as e:
                    result = ToolResult(success=False, output="", error=str(e))
                finally:
                    cs.app_proxy._chain_confirm_callback = None
                    cs.app_proxy._chain_ask_user_callback = None
                    cs.app_proxy._chain_event_callback = None

                # 发送 chain_call_end 事件
                yield _sse({
                    "type": "chain_call_end",
                    "agent_name": _target_agent,
                    "success": result.success,
                })
            else:
                try:
                    # v5.2.9：工具执行期间轮询 abort，可中断工具（terminal）
                    # 在用户中断时立即杀子进程（旧版须等工具执行完毕）
                    result = await _execute_tool_interruptible(
                        cs, tool_name, tool_args)
                except Exception as e:
                    result = ToolResult(success=False, output="", error=str(e))
            result.duration_ms = int((_time.monotonic() - _t0) * 1000)

            # v5.2.9：用户在工具执行期间中断——记录结果并立即结束本轮运行
            # （跳过反思重试与剩余工具；被中断的命令不按普通失败处理）
            if cs.abort:
                _int_out = (result.output or "")
                if result.error:
                    _int_out += f"\n错误: {result.error}"
                _int_out = _int_out[:MAX_TOOL_OUTPUT_LENGTH]
                yield _sse({
                    "type": "tool_result", "tool_name": tool_name,
                    "tool_id": tool_id, "success": False,
                    "preview": _int_out[:7500],
                    "duration_ms": getattr(result, "duration_ms", 0),
                })
                cs.session.add_message(
                    "tool", _int_out or "命令已被用户中断",
                    tool_call_id=tool_id,
                    metadata={"tool_name": tool_name, "success": False})
                _fill_aborted_tool_msgs(cs, valid_calls)
                yield _sse({"type": "aborted"})
                return

            # ---- Web 端：python 工具执行后检测新生成的图片文件 ----
            if tool_name == "python" and result.success:
                _new_images = []
                for _dir in (os.getcwd(), '/tmp'):
                    try:
                        for _f in os.listdir(_dir):
                            if os.path.splitext(_f)[1].lower() in _img_exts:
                                _fp = os.path.join(_dir, _f)
                                if _fp not in _pre_images:
                                    _new_images.append({
                                        "path": _fp,
                                        "filename": _f,
                                        "is_image": True,
                                    })
                    except Exception:
                        pass
                if _new_images:
                    if not result.display_files:
                        result.display_files = []
                    result.display_files.extend(_new_images)

            # ---- Web 端：write/edit 成功后自动添加文件到 display_files ----
            if result.success and tool_name in ("write", "edit"):
                _fp = tool_args.get("file_path", "")
                if _fp:
                    _fname = os.path.basename(_fp)
                    _ext = os.path.splitext(_fname)[1].lower()
                    _is_img = _ext in _img_exts
                    if not result.display_files:
                        result.display_files = []
                    result.display_files.append({
                        "path": _fp,
                        "filename": _fname,
                        "is_image": _is_img,
                    })

            # ---- PostToolUse 钩子（反馈追加给模型）----
            if cs.hook_manager and cs.hook_manager.has_hooks("PostToolUse"):
                decision = await asyncio.to_thread(
                    cs.hook_manager.run_post_tool_use,
                    tool_name, tool_args,
                    (result.output or result.error or "")[:4000],
                    cs.session.id)
                feedback = decision.merged_output()
                if feedback:
                    result.output = (result.output or "") + \
                        f"\n\n[PostToolUse 钩子反馈]\n{feedback}"

            # ---- 可观测性 trace ----
            if cs.tracer:
                cs.tracer.log_tool_call(
                    tool_name, tool_args, permission=action,
                    duration_ms=result.duration_ms,
                    success=result.success, error=result.error or "")

            if result.success:
                tool_output = (result.output or "")[:MAX_TOOL_OUTPUT_LENGTH]
            else:
                tool_output = (result.output or f"错误: {result.error}")[:MAX_TOOL_OUTPUT_LENGTH]

            # 死循环软警告附加到工具结果尾部（告知模型换策略）
            if verdict == "warn" and loop_msg:
                tool_output = f"{tool_output}\n\n{loop_msg}"

            # ---- 自我反思（与 CLI 一致）----
            if not result.success:
                failure_counts[tool_name] = failure_counts.get(tool_name, 0) + 1
                retry_count = failure_counts[tool_name]
                if retry_count <= MAX_REFLECTION_RETRIES:
                    yield _sse({
                        "type": "reflection", "tool_name": tool_name,
                        "retry": retry_count, "max_retries": MAX_REFLECTION_RETRIES,
                    })
                    tool_output = (
                        f"[反思提示] 上一个工具调用失败，请分析原因并重试。\n"
                        f"失败工具: {tool_name}\n"
                        f"参数: {json.dumps(tool_args, ensure_ascii=False)}\n"
                        f"剩余重试: {MAX_REFLECTION_RETRIES - retry_count}/{MAX_REFLECTION_RETRIES}\n\n"
                        f"--- 原始输出 ---\n{tool_output}"
                    )
            else:
                failure_counts.pop(tool_name, None)

            # ---- 结构化预览 ----
            preview = tool_output[:7500] if len(tool_output) > 7500 else tool_output
            preview_data = None
            if tool_name == "edit" and result.success:
                preview_data = {
                    "type": "edit",
                    "file_path": tool_args.get("file_path", ""),
                    "old_str": tool_args.get("old_str", ""),
                    "new_str": tool_args.get("new_str", ""),
                }
            elif tool_name == "write" and result.success:
                preview_data = {
                    "type": "write",
                    "file_path": tool_args.get("file_path", ""),
                    "content": tool_args.get("content", ""),
                }
            elif tool_name == "python":
                preview_data = {
                    "type": "python",
                    "code": tool_args.get("code", ""),
                    "output": preview,
                    "success": result.success,
                }

            sse_data = {
                "type": "tool_result",
                "tool_name": tool_name,
                "tool_id": tool_id,
                "success": result.success,
                "preview": preview,
                "duration_ms": getattr(result, "duration_ms", 0),
            }
            if preview_data:
                sse_data["preview_data"] = preview_data
            # Web 端：AI 发送给用户的文件/图片（display_files）
            if result.display_files:
                display_files_with_url = []
                for df in result.display_files:
                    entry = dict(df)
                    fp = df.get("path", "")
                    # 编码路径用于 URL 传参
                    import urllib.parse
                    encoded_path = urllib.parse.quote(fp, safe="")
                    if df.get("is_image"):
                        entry["url"] = f"/api/files/serve_path?path={encoded_path}"
                    entry["download_url"] = f"/api/files/download_path?path={encoded_path}"
                    display_files_with_url.append(entry)
                sse_data["display_files"] = display_files_with_url
            yield _sse(sse_data)

            cs.session.add_message(
                "tool", tool_output, tool_call_id=tool_id,
                metadata={"tool_name": tool_name, "success": result.success})

            # 工具结果携带图片（image 工具直发模式）：延迟到所有 tool 消息之后
            # 统一追加带图用户消息（避免插在 tool 消息之间导致 API 报错）
            if result.success and getattr(result, "images", None):
                vision_prompt = (result.metadata or {}).get("vision_prompt", "")
                note = f"[image 工具传入 {len(result.images)} 张图片]"
                if vision_prompt:
                    note += f" 识别需求: {vision_prompt}"
                pending_image_msgs.append((note, result.images))

        # ---- 所有工具执行完毕：统一追加图片 user 消息（避免插队破坏消息序列）----
        for note, images in pending_image_msgs:
            cs.session.add_message("user", note, images=images)

        # ---- 死循环熔断：终止本轮任务 ----
        if loop_aborted:
            yield _sse({"type": "error",
                        "content": "🛑 检测到模型多次陷入死循环，已熔断本轮任务。"
                                   "建议换一种任务描述或切换模型。"})
            yield _sse({"type": "done", "usage": cs.usage_stats()})
            return

        # 继续下一轮 ReAct

    yield _sse({"type": "content", "content": "\n\n[达到最大工具调用轮数]"})
    yield _sse({"type": "done", "usage": cs.usage_stats()})


@app.post("/api/chat")
async def chat(req: ChatRequest):
    """发送消息并启动后台 ReAct 运行（v5.2.9 实时架构）。

    立即返回，不再持有 HTTP 流：完整 ReAct 循环在后台 asyncio 任务中
    执行（_background_react_runner），全部事件经会话事件总线广播给
    所有已订阅该会话的浏览器（WebSocket /ws）。
    - 浏览器关闭/切换会话不影响任务继续执行
    - 多个浏览器打开同一会话看到完全一致的实时画面
    """
    cs = _resolve_session(req.agent_name, req.model_name, req.session_id or "")

    if cs.lock and cs.lock.locked():
        raise HTTPException(409, "该会话正在处理中，请等待完成或先中断")

    # UserPromptSubmit 钩子：stdout 追加为用户上下文（与 CLI 一致）
    user_message = req.message
    if cs.hook_manager and cs.hook_manager.has_hooks("UserPromptSubmit"):
        try:
            decision = await asyncio.to_thread(
                cs.hook_manager.run_simple, "UserPromptSubmit",
                session_id=cs.session.id,
                extra_args={"prompt": req.message})
            extra = decision.merged_output()
            if extra:
                user_message = f"{user_message}\n\n[钩子补充上下文]\n{extra}"
        except Exception:
            pass

    cs.session.add_message("user", user_message,
                           images=req.images if req.images else None)
    cs.abort = False

    # 后台运行（事件总线发布 run_start + SessionStart 钩子输出）
    _start_background_run(cs, req.message or "")

    return {"session_id": cs.session.id, "message": "已接收",
            "started": True}


@app.post("/api/chat/respond")
async def chat_respond(req: ChatRespondRequest):
    """工具确认 / ask_user 应答（v5.2.9：多浏览器首个应答生效）。"""
    cs = _resolve_session_quiet(req.agent_name, req.model_name, req.session_id or "")
    if cs and cs.respond_queue is not None:
        # 仅在会话确实等待应答时接收（其他浏览器已应答/已超时则忽略，
        # 防止迟到的应答被误当作下一次确认的结果）
        if not getattr(cs, "respond_waiting", False):
            return {"message": "无待处理操作"}
        await cs.respond_queue.put(req.response)
        # 广播应答事件：所有浏览器撤销待确认/待回答 UI
        _publish_event(cs, {"type": "responded",
                            "session_id": cs.session.id,
                            "response": req.response})
        return {"message": "应答已接收"}
    return {"message": "无待处理操作"}


@app.post("/api/chat/reset")
async def reset_chat(req: Request):
    """新建会话（v5.2.9：旧会话不中断、不弹栈，后台继续运行）。

    - 旧会话每轮对话已自动落盘（autosave），无需在此保存
    - 旧会话若仍在运行，继续在后台执行；空闲则留在会话注册表中，
      侧边栏仍可点开（数据与历史一致）
    - 仅当旧会话空闲时清理其 python 解释器会话；运行中保留
    """
    body = await req.json()
    agent_name = body.get("agent_name", "")
    model_name = body.get("model_name", "")
    session_id = body.get("session_id", "")
    key = _get_session_key(agent_name, model_name)

    old = None
    if session_id:
        old = _sessions_by_id.get(session_id)
    if old is None:
        old = _chat_sessions.get(key)

    if old is not None and not getattr(old, "run_active", False):
        try:
            remove_python_session(old.session.id)
        except Exception:
            pass

    # 创建全新会话并注册为该 (agent, model) 的默认会话
    cs = WebChatSession.create(agent_name, model_name)
    _chat_sessions[key] = cs
    _register_session(cs)
    _broadcast_global({"type": "sessions_changed"})
    return {"message": "已开始新会话", "session_id": cs.session.id,
            "usage": cs.usage_stats()}


@app.post("/api/chat/switch_model")
async def chat_switch_model(req: Request):
    """原地切换模型，保留当前会话全部内容（对齐 CLI /model use → switch_model）。

    仅替换 LLM 客户端及关联组件（token计数/压缩器/上下文窗口限制），
    会话消息原样保留，系统提示原地更新（模型名称、视觉能力描述可能变化）。
    v5.2.9：支持 session_id 定位会话（后台运行的会话也可原地切模型）。
    """
    body = await req.json()
    agent_name = body.get("agent_name", "")
    old_model = body.get("old_model", "")
    new_model = body.get("new_model", "")
    session_id = body.get("session_id", "")

    if not agent_name or not new_model:
        raise HTTPException(400, "缺少 agent_name / new_model")

    config = get_config()
    model_config = config.get_model(new_model)
    if not model_config:
        raise HTTPException(404, f"模型 '{new_model}' 不存在")
    config.set_last_selected_model(new_model)

    old_key = _get_session_key(agent_name, old_model or new_model)
    new_key = _get_session_key(agent_name, new_model)

    # v5.2.9：优先按 session_id 定位（当前订阅的会话可能不是默认会话）
    cs = None
    if session_id:
        cs = _sessions_by_id.get(session_id)
        if cs is not None and cs.agent_name != agent_name:
            cs = None
    if cs is None:
        cs = _chat_sessions.get(old_key)
    if cs is None or old_key == new_key:
        # 无活动会话（或新旧模型相同），仅更新选择，无需迁移
        return {"switched": False, "message": f"已选择模型 '{new_model}'"}

    # 新键位若已有旧会话：让位（保留在注册表继续后台运行，不中断，v5.2.9）
    existing = _chat_sessions.pop(new_key, None)
    if existing is not None and existing is not cs:
        _register_session(existing)

    # 原地替换模型组件（会话消息原样保留）
    cs.llm_client = LLMClient(model_config)
    cs.token_counter = get_token_counter(model_config.get("model"))
    cs.context_compressor = ContextCompressor(
        cs.llm_client, cs.token_counter,
        workspace_path=getattr(cs.agent_config, "workspace_path", None))
    if cs.context_window:
        cs.context_window.model_limit = cs.llm_client.context_limit
    if cs.app_proxy:
        cs.app_proxy.llm_client = cs.llm_client
        cs.app_proxy.token_counter = cs.token_counter
        cs.app_proxy.context_compressor = cs.context_compressor
    cs.model_name = new_model
    cs.session_key = new_key
    cs._rebuild_system_prompt()  # 原地更新首条 system 消息

    # 会话键迁移: (agent, old_model) -> (agent, new_model)（仅当 cs 是旧默认会话）
    if _chat_sessions.get(old_key) is cs:
        _chat_sessions.pop(old_key, None)
        _chat_sessions[new_key] = cs

    return {"switched": True,
            "message": f"已切换到模型 '{new_model}'（会话及上下文已保留）",
            "session_id": cs.session.id,
            "usage": cs.usage_stats()}

@app.post("/api/chat/abort")
async def chat_abort(req: Request):
    """中断当前流式响应。"""
    body = await req.json()
    agent_name = body.get("agent_name", "")
    model_name = body.get("model_name", "")
    session_id = body.get("session_id", "")
    cs = _resolve_session_quiet(agent_name, model_name, session_id)
    if cs:
        # wait_response 每 0.5s 轮询一次 abort 标志，无需唤醒队列
        # （向队列投放空串会被误判为用户确认，导致中断失效继续执行工具）
        cs.abort = True
    return {"message": "已请求中断"}


@app.get("/api/chat/status")
def chat_status(agent_name: str, model_name: str, session_id: str = ""):
    """会话状态：token 精确统计 + 上下文占比 + 后台运行状态（v5.2.9）。"""
    cs = _resolve_session_quiet(agent_name, model_name, session_id)
    if not cs:
        # 会话不存在（页面刷新后），也从 GlobalConfig 返回持久化的链条状态
        result = {
            "active": False, "message_count": 0, "token_estimate": 0,
            "ctx_percentage": 0.0, "model_limit": 0, "remaining_tokens": 0,
            "tool_call_count": 0, "cwd": os.getcwd(),
            "workspace": _current_workspace,
        }
        try:
            saved_chain = get_config().get_active_chain(agent_name)
            result["active_chain"] = saved_chain or None
        except Exception:
            result["active_chain"] = None
        return result
    stats = cs.usage_stats()
    stats.update({"active": True, "cwd": os.getcwd(),
                  "workspace": _current_workspace,
                  "session_id": cs.session.id,
                  "busy": bool(cs.lock and cs.lock.locked()),
                  "run_active": bool(getattr(cs, "run_active", False))})
    # 链条激活状态（前端据此更新按钮）
    if cs.active_chain:
        stats["active_chain"] = cs.active_chain.name
    else:
        stats["active_chain"] = None
    return stats


@app.get("/api/chat/messages")
def chat_messages(agent_name: str, model_name: str, session_id: str = ""):
    """导出当前会话消息（前端刷新后恢复对话）。

    v5.2.9：会话后台运行中时截断到本次运行开始处，当前运行的内容
    由 WebSocket 事件日志回放渲染（避免双份展示）。
    """
    cs = _resolve_session_quiet(agent_name, model_name, session_id)
    if not cs:
        return {"messages": []}
    return {"messages": cs.export_messages(_live_export_limit(cs)),
            "session_id": cs.session.id,
            "run_active": bool(getattr(cs, "run_active", False)),
            "run_seq": getattr(cs, "run_seq", 0)}


def _live_session_stale_vs_disk(cs: 'WebChatSession', filename: str) -> bool:
    """内存会话空闲且磁盘文件被外部更新过（如 CLI 对话）-> 内存副本过期。

    CLI 与 Web 是两个独立进程、各自持有内存会话，共享历史文件。若磁盘文件
    mtime 晚于本会话最近活动时间，说明外部进程写入了更新内容——必须丢弃
    内存副本改从磁盘重载，否则 Web 页面显示过时/分叉内容（v5.2.9 修复：
    CLI 对话中 Web 打开同一会话看到的是旧快照 + 测试残留）。
    """
    if getattr(cs, "run_active", False):
        return False
    try:
        if cs.lock and cs.lock.locked():
            return False
    except Exception:
        pass
    try:
        agent_config = _get_agent_config(cs.agent_name)
        if not agent_config:
            return False
        fp = SessionHistoryManager(agent_config.workspace_path).history_dir / filename
        if not fp.exists():
            return False
        return fp.stat().st_mtime > getattr(cs, "last_active_ts", 0.0) + 1.0
    except Exception:
        return False


def _load_session_core(agent_name: str, model_name: str, filename: str):
    """加载历史会话为当前会话（v5.2.8，chat_load 与 workspace/open 共用）。

    【保留原会话 id/创建时间/工作空间/标题】：后续自动保存幂等覆盖同一文件，
    对该会话的新问答都记录在该会话下（点击会话=直接跳转，不产生副本）。
    v5.2.9：切换会话【不再中断旧会话】（每轮已自动保存落盘，后台任务
    继续执行）；若同 id 会话仍在内存中（含后台运行中），直接复用该会话
    对象--保留运行状态与未落盘的当前轮，绝不重建副本。返回 (cs, data)。
    """
    agent_config = _get_agent_config(agent_name)
    if not agent_config:
        raise HTTPException(404, f"Agent '{agent_name}' 不存在")

    history_mgr = SessionHistoryManager(agent_config.workspace_path)
    data = history_mgr.load_session_full(filename)
    if data is None:
        raise HTTPException(404, "会话不存在")

    key = _get_session_key(agent_name, model_name)

    # v5.2.9：同 id 会话仍在内存（含后台运行）-> 直接复用，保留运行状态；
    # 但内存副本过期时（磁盘被 CLI 等外部进程更新）落到下方从磁盘重载
    orig_id_live = data.get("id") or ""
    if orig_id_live:
        live = _sessions_by_id.get(orig_id_live)
        if live is not None and live.agent_name == agent_name \
                and not _live_session_stale_vs_disk(live, filename):
            _chat_sessions[key] = live
            return live, data

    # 旧默认会话让位（不中断：可能仍在后台运行，v5.2.9）
    _chat_sessions.pop(key, None)

    cs = WebChatSession.create(agent_name, model_name)
    orig_id = data.get("id") or ""
    if orig_id:
        cs.session.id = orig_id
        try:
            if cs.tracer:
                cs.tracer.session_id = orig_id
        except Exception:
            pass
        try:
            cs.app_proxy.tool_executor.session_id = orig_id
        except Exception:
            pass
        cs._sync_python_session()  # v5.2.9：id 覆盖后重新隔离 python 会话
    try:
        if data.get("created_at"):
            cs.session.created_at = datetime.fromisoformat(data["created_at"])
    except Exception:
        pass
    cs.workspace = data.get("workspace") or str(_SERVER_ROOT)
    cs.custom_title = data.get("title") or ""
    for msg in data.get("messages", []):
        role = msg.get("role", "")
        content = msg.get("content", "") or ""
        # 跳过 system 消息（保留新建系统提示，含最新 skills/tools.md），
        # 但保留上下文压缩生成的历史摘要消息——摘要是 agent 对早期对话的记忆，
        # 丢弃它会导致恢复压缩过的会话后 agent 失忆（v5.2.3 修复）
        if role == "system" and not content.startswith(SUMMARY_MARKER):
            continue
        cs.session.add_message(
            role, content,
            tool_call_id=msg.get("tool_call_id"),
            tool_calls=msg.get("tool_calls"),
            reasoning_content=msg.get("reasoning_content"),
        )
    _chat_sessions[key] = cs
    _register_session(cs)
    import time as _time
    cs.last_active_ts = _time.time()  # 过期检测基线：刚加载不算过期
    return cs, data


@app.post("/api/chat/load")
async def chat_load(req: Request):
    """加载历史会话为当前会话（完整重建压缩组件，修复旧版缺陷）。"""
    body = await req.json()
    agent_name = body.get("agent_name", "")
    model_name = body.get("model_name", "")
    filename = body.get("filename", "")
    session_id = (body.get("session_id") or "").strip()

    if not agent_name or not model_name or (not filename and not session_id):
        raise HTTPException(400, "缺少 agent_name / model_name / filename")

    # v5.2.9：该 id 的会话仍在内存（含后台运行中）-> 直接复用。
    # 保留其运行状态与未落盘的当前轮；运行中的会话事件由 WS 回放接管。
    # 内存副本过期时（磁盘被 CLI 等外部进程更新）落到下方文件路径从磁盘重载。
    if session_id:
        live = _sessions_by_id.get(session_id)
        if live is not None and live.agent_name == agent_name:
            fname = _find_history_file_by_id(agent_name, session_id)
            # v5.2.9 修复：磁盘无文件（新建会话第一轮运行中，尚未落盘）时，
            # 内存副本是唯一真相来源（不存在外部进程更新磁盘的过期问题），
            # 直接复用；此前 fname=None 会跳过复用落入 404"会话不存在"。
            if fname is None or not _live_session_stale_vs_disk(live, fname):
                _chat_sessions[_get_session_key(agent_name, model_name)] = live
                ws = (body.get("workspace") or "").strip() or live.workspace
                if ws and Path(ws).is_dir():
                    try:
                        _set_current_workspace(ws)
                    except Exception:
                        pass
                return {"message": "会话已加载", "session_id": live.session.id,
                        "messages": live.export_messages(_live_export_limit(live)),
                        "usage": live.usage_stats(),
                        "model": live.model_name,
                        "workspace": _current_workspace,
                        "run_active": bool(getattr(live, "run_active", False)),
                        "run_seq": getattr(live, "run_seq", 0)}
            if fname:
                filename = fname  # 副本过期：改走文件加载路径从磁盘重载

    # v5.2.8：无文件名时按会话 id 定位（服务器重启后前端自动找回原会话）
    if not filename and session_id and not any(c in session_id for c in "*?[]"):
        agent_config = _get_agent_config(agent_name)
        if agent_config:
            hist_dir = SessionHistoryManager(
                agent_config.workspace_path).history_dir
            found = sorted(hist_dir.glob(f"*_{session_id}.json"), reverse=True)
            if found:
                filename = found[0].name
    if not filename:
        raise HTTPException(404, "会话不存在")

    cs, data = _load_session_core(agent_name, model_name, filename)

    # v5.2.8：加载会话时同步切换到其所属工作空间（Agent 随之知道新工作目录）
    ws = (body.get("workspace") or "").strip() or cs.workspace
    if ws and Path(ws).is_dir():
        try:
            _set_current_workspace(ws)
        except Exception:
            pass

    return {"message": "会话已加载",
            "session_id": cs.session.id,
            "messages": cs.export_messages(_live_export_limit(cs)),
            "usage": cs.usage_stats(),
            "model": cs.model_name,
            "workspace": _current_workspace,
            "run_active": bool(getattr(cs, "run_active", False)),
            "run_seq": getattr(cs, "run_seq", 0)}


# ===================================================================
#  工作空间管理（v5.2.8：侧边栏会话按工作空间分组）
# ===================================================================

def _is_under(p: Path, root: Path) -> bool:
    try:
        p.relative_to(root)
        return True
    except Exception:
        return False


def _ws_display_name(path: str) -> str:
    """工作空间显示名：相对服务器根目录的路径（根目录本身用目录名）。"""
    try:
        rel = Path(path).resolve().relative_to(_SERVER_ROOT)
        if str(rel) == ".":
            return _SERVER_ROOT.name or str(_SERVER_ROOT)
        return str(rel)
    except Exception:
        return Path(path).name or str(path)


@app.get("/api/workspace/info")
def workspace_info(agent_name: str):
    """工作空间列表：按工作空间目录分组，组内为该目录下的会话（含活跃会话）。"""
    history_mgr = SessionHistoryManager(_get_agent_workspace(agent_name))
    saved = history_mgr.list_sessions(limit=500)

    groups: "dict[str, dict]" = {}

    def group_of(ws: str) -> dict:
        ws = ws or str(_SERVER_ROOT)
        if ws not in groups:
            groups[ws] = {"path": ws, "name": _ws_display_name(ws),
                          "sessions": []}
        return groups[ws]

    # 活跃会话（内存注册表全部会话，含后台运行中的；优先于历史文件展示）
    # v5.2.9：展示有内容/正在运行/当前默认的会话（其余全新空会话不进侧边栏）
    seen_ids = set()
    default_cs_ids = {id(v) for v in _chat_sessions.values()}
    for cs in list(_sessions_by_id.values()):
        if cs.agent_name != agent_name or not cs.session:
            continue
        has_content = len(cs.session.messages) > 1
        running = bool(getattr(cs, "run_active", False))
        is_default = id(cs) in default_cs_ids
        if not has_content and not running and not is_default:
            continue
        seen_ids.add(cs.session.id)
        group_of(getattr(cs, "workspace", "") or "")["sessions"].append({
            "filename": "", "id": cs.session.id, "title": _session_title(cs),
            "created_at": cs.session.created_at.isoformat(),
            "message_count": len(cs.session.messages),
            "workspace": getattr(cs, "workspace", "") or str(_SERVER_ROOT),
            "model": cs.model_name, "active": True, "running": running,
        })

    # 历史会话（与活跃会话同 id 的跳过，活跃条目更新）
    for s in saved:
        if s.get("id") in seen_ids:
            continue
        entry = dict(s)
        entry["active"] = False
        entry["running"] = False
        group_of(s.get("workspace", "") or "")["sessions"].append(entry)

    # 确保当前工作空间 + 历史打开过的工作空间始终在列表中
    # （打开过的工作空间常驻侧边栏，不因切换到新工作空间而消失）
    opened = [p for p in _load_opened_workspaces() if Path(p).is_dir()]
    group_of(_current_workspace)
    for p in opened:
        group_of(p)

    # 组内按时间倒序
    for g in groups.values():
        g["sessions"].sort(key=lambda x: x.get("created_at") or "", reverse=True)

    # 排序：当前工作空间优先 → 按最近打开顺序 → 仅历史会话出现的按最新会话倒序
    cur = [g for g in groups.values() if g["path"] == _current_workspace]
    seen = {g["path"] for g in cur}
    opened_rest = []
    for p in opened:
        g = groups.get(p)
        if g and p not in seen:
            opened_rest.append(g)
            seen.add(p)
    hist_rest = [g for g in groups.values() if g["path"] not in seen]
    hist_rest.sort(key=lambda g: (g["sessions"][0]["created_at"]
                                  if g["sessions"] else ""), reverse=True)
    workspaces = cur + opened_rest + hist_rest

    return {"server_root": str(_SERVER_ROOT),
            "current": _current_workspace,
            "workspaces": workspaces}


@app.get("/api/workspace/browse")
def workspace_browse(path: str = ""):
    """列出目录下的子文件夹（打开工作空间的选择弹窗），限定服务器根目录内。"""
    base = Path(path).resolve() if path else _SERVER_ROOT
    if not (base == _SERVER_ROOT or _is_under(base, _SERVER_ROOT)):
        raise HTTPException(403, "只能浏览当前目录下的文件夹")
    if not base.is_dir():
        raise HTTPException(404, "目录不存在")
    dirs = []
    for d in sorted(base.iterdir(), key=lambda p: p.name.lower()):
        try:
            if d.is_dir() and not d.name.startswith("."):
                dirs.append({"name": d.name, "path": str(d.resolve())})
        except Exception:
            continue
    return {"path": str(base), "server_root": str(_SERVER_ROOT),
            "relative": _ws_display_name(str(base)), "dirs": dirs}


@app.post("/api/workspace/open")
async def workspace_open(req: Request):
    """打开文件夹作为工作空间：保存当前会话 + 切换工作目录。"""
    body = await req.json()
    agent_name = body.get("agent_name", "")
    model_name = body.get("model_name", "")
    path = (body.get("path") or "").strip()
    if not agent_name or not model_name or not path:
        raise HTTPException(400, "缺少 agent_name / model_name / path")
    target = Path(path).resolve()
    if not (target == _SERVER_ROOT or _is_under(target, _SERVER_ROOT)):
        raise HTTPException(403, "只能打开当前目录下的文件夹作为工作空间")
    if not target.is_dir():
        raise HTTPException(404, "目录不存在")

    # 旧默认会话让位（v5.2.9：不中断、不弹栈；每轮已自动落盘，
    # 仍运行中的会话继续后台执行，侧边栏仍可点开）
    key = _get_session_key(agent_name, model_name)
    _chat_sessions.pop(key, None)

    _set_current_workspace(str(target))

    # resume=True（侧边栏"选择该文件夹"）：恢复该工作空间下最新会话
    messages_out: list = []
    usage_out = None
    session_out = None
    model_out = None
    run_active_out = False
    run_seq_out = 0
    if body.get("resume"):
        history_mgr = SessionHistoryManager(_get_agent_workspace(agent_name))
        latest = None
        for s in history_mgr.list_sessions(limit=200):
            if (s.get("workspace") or str(_SERVER_ROOT)) == str(target):
                latest = s
                break
        if latest:
            cs, _ = _load_session_core(agent_name, model_name, latest["filename"])
            messages_out = cs.export_messages(_live_export_limit(cs))
            usage_out = cs.usage_stats()
            session_out = cs.session.id
            model_out = cs.model_name
            run_active_out = bool(getattr(cs, "run_active", False))
            run_seq_out = getattr(cs, "run_seq", 0)

    return {"message": f"已打开工作空间: {_ws_display_name(str(target))}",
            "workspace": _current_workspace,
            "messages": messages_out, "usage": usage_out,
            "session_id": session_out, "model": model_out,
            "run_active": run_active_out, "run_seq": run_seq_out}


@app.post("/api/workspace/sessions/clear")
async def workspace_sessions_clear(req: Request):
    """删除某工作空间下的全部会话（v5.2.8 文件夹三点菜单）。"""
    body = await req.json()
    agent_name = body.get("agent_name", "")
    path = (body.get("path") or "").strip()
    if not agent_name or not path:
        raise HTTPException(400, "缺少 agent_name / path")
    target = str(Path(path).resolve())
    history_mgr = SessionHistoryManager(_get_agent_workspace(agent_name))
    removed = 0
    for s in history_mgr.list_sessions(limit=1000):
        if (s.get("workspace") or str(_SERVER_ROOT)) == target:
            if history_mgr.delete_session(s["filename"]):
                removed += 1
    was_active = False
    for key in list(_chat_sessions.keys()):
        cs = _chat_sessions[key]
        if cs.agent_name == agent_name and \
                (getattr(cs, "workspace", "") or "") == target:
            cs.abort = True
            _chat_sessions.pop(key, None)
            was_active = True
    return {"message": f"已删除 {removed} 个会话",
            "removed": removed, "was_active": was_active}


@app.get("/api/files/list")
def files_list(path: str = ""):
    """文件管理器：列出目录下的文件和文件夹（v5.2.8，限定当前工作空间内）。"""
    ws = Path(_current_workspace).resolve()
    base = Path(path).resolve() if path else ws
    if not (base == ws or _is_under(base, ws)):
        raise HTTPException(403, "只能浏览当前工作空间内的文件")
    if not base.is_dir():
        raise HTTPException(404, "目录不存在")
    try:
        items = sorted(base.iterdir(),
                       key=lambda p: (not p.is_dir(), p.name.lower()))
    except Exception as e:
        raise HTTPException(500, f"读取目录失败: {e}")
    entries = []
    for p in items:
        try:
            st = p.stat()
            entries.append({
                "name": p.name,
                "path": str(p.resolve()),
                "is_dir": p.is_dir(),
                "size": 0 if p.is_dir() else st.st_size,
                "mtime": round(st.st_mtime),
            })
        except Exception:
            continue
    return {"path": str(base), "workspace": str(ws), "entries": entries}


def _find_live_session(agent_name: str, session_id: str):
    """按会话 id 查找内存中的活跃会话（v5.2.9：从全会话注册表查找）。"""
    cs = _sessions_by_id.get(session_id)
    if cs is not None and cs.agent_name == agent_name and cs.session \
            and cs.session.id == session_id:
        return cs
    return None


@app.post("/api/workspace/session/rename")
async def session_rename(req: Request):
    """重命名会话（v5.2.8 侧边栏会话管理）。"""
    body = await req.json()
    agent_name = body.get("agent_name", "")
    session_id = body.get("session_id", "")
    filename = body.get("filename", "")
    # 压缩换行/连续空白为单空格（标题保持单行）
    title = " ".join((body.get("title") or "").split())
    if not agent_name or not title:
        raise HTTPException(400, "缺少 agent_name / title")
    history_mgr = SessionHistoryManager(_get_agent_workspace(agent_name))
    live = _find_live_session(agent_name, session_id) if session_id else None
    if live:
        live.custom_title = title
    if filename:
        if not history_mgr.update_session_title(filename, title):
            raise HTTPException(404, "会话不存在")
    elif live and live.session and len(live.session.messages) > 1:
        # 活跃会话尚未落盘时立即保存以持久化标题
        cfg = _get_agent_config(agent_name)
        if cfg:
            SessionHistoryManager(cfg.workspace_path).save_session(
                live.session.get_context_messages(), live.session.id,
                workspace=getattr(live, "workspace", "") or "", title=title)
    _broadcast_global({"type": "sessions_changed"})
    return {"message": "已重命名"}


@app.post("/api/workspace/session/delete")
async def session_delete(req: Request):
    """删除会话（若为活跃会话同时清除内存对话）。"""
    body = await req.json()
    agent_name = body.get("agent_name", "")
    session_id = body.get("session_id", "")
    filename = body.get("filename", "")
    if not agent_name:
        raise HTTPException(400, "缺少 agent_name")
    history_mgr = SessionHistoryManager(_get_agent_workspace(agent_name))
    deleted = False
    if filename:
        deleted = history_mgr.delete_session(filename)
    elif session_id and not any(c in session_id for c in "*?[]"):
        # 无文件名时按会话 id 删除历史文件（复制产生的副本等）
        for fp in history_mgr.history_dir.glob(f"*_{session_id}.json"):
            try:
                fp.unlink()
                deleted = True
            except Exception:
                pass
    live = _find_live_session(agent_name, session_id) if session_id else None
    if live:
        # v5.2.9：中断后台任务 + 从注册表彻底移除 + 清理 python 会话
        live.abort = True
        task = getattr(live, "run_task", None)
        if task is not None and not task.done():
            task.cancel()
        _sessions_by_id.pop(session_id, None)
        for k, v in list(_chat_sessions.items()):
            if v is live:
                _chat_sessions.pop(k, None)
                break
        try:
            remove_python_session(session_id)
        except Exception:
            pass
        _broadcast_global({"type": "sessions_changed"})
        deleted = True
    if not deleted:
        raise HTTPException(404, "会话不存在")
    return {"message": "已删除", "was_active": bool(live)}


@app.post("/api/workspace/session/copy")
async def session_copy(req: Request):
    """复制会话（新 id 独立副本，可再重命名/分支对话）。"""
    body = await req.json()
    agent_name = body.get("agent_name", "")
    session_id = body.get("session_id", "")
    filename = body.get("filename", "")
    if not agent_name:
        raise HTTPException(400, "缺少 agent_name")
    history_mgr = SessionHistoryManager(_get_agent_workspace(agent_name))
    live = _find_live_session(agent_name, session_id) if session_id else None
    if live:
        if not live.session or len(live.session.messages) <= 1:
            raise HTTPException(400, "当前会话为空，无法复制")
        new_id = str(uuid.uuid4())
        history_mgr.save_session(
            live.session.get_context_messages(), new_id,
            workspace=getattr(live, "workspace", "") or "",
            title=getattr(live, "custom_title", "") or "")
        return {"message": "已复制", "new_id": new_id}
    if not filename:
        raise HTTPException(400, "缺少 filename")
    data = history_mgr.load_session_full(filename)
    if data is None:
        raise HTTPException(404, "会话不存在")
    new_id = str(uuid.uuid4())
    history_mgr.save_session(
        data.get("messages", []), new_id,
        workspace=data.get("workspace", "") or "",
        title=data.get("title", "") or "")
    return {"message": "已复制", "new_id": new_id}


@app.post("/api/chat/compress")
async def chat_compress(req: Request):
    """手动压缩上下文（对应 CLI /comp）。"""
    body = await req.json()
    agent_name = body.get("agent_name", "")
    model_name = body.get("model_name", "")
    cs = _resolve_session_quiet(agent_name, model_name, body.get("session_id", ""))
    if not cs:
        raise HTTPException(400, "当前没有活动会话")
    if not cs.context_compressor:
        raise HTTPException(400, "压缩组件未初始化")

    before = cs.session.get_total_tokens(cs.token_counter)
    instructions = body.get("instructions", "") or None
    try:
        success = await asyncio.to_thread(
            cs.context_compressor.compress,
            cs.session, cs.context_window.compression_target(),
            instructions)
    except Exception as e:
        raise HTTPException(500, f"压缩失败: {e}")

    if not success:
        # 区分"压缩失败"和"无需压缩"：last_error 有值说明摘要生成真的失败了
        err = getattr(cs.context_compressor, "last_error", None)
        if err:
            return {"message": f"压缩失败: {err}", "compressed": False,
                    "usage": cs.usage_stats()}
        return {"message": "上下文较短，无需压缩", "compressed": False,
                "usage": cs.usage_stats()}

    after = cs.session.get_total_tokens(cs.token_counter)
    cs.context_window.update(after)
    return {"message": f"上下文已压缩（{before:,} → {after:,} tokens）",
            "compressed": True, "usage": cs.usage_stats()}


@app.post("/api/chat/upload")
async def chat_upload(file: UploadFile = File(...),
                      agent_name: str = Form(...), model_name: str = Form(...)):
    """上传文件/图片供下一条消息使用。"""
    if not file.filename:
        raise HTTPException(400, "未提供文件")

    content = await file.read()
    max_size = 10 * 1024 * 1024
    if len(content) > max_size:
        raise HTTPException(400, "文件过大（最大 10MB）")

    upload_dir = CBHCLI_DIR / "web_uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)

    safe_name = file.filename.replace("/", "_").replace("\\", "_")
    dest = upload_dir / safe_name
    counter = 1
    while dest.exists():
        stem = Path(safe_name).stem
        suffix = Path(safe_name).suffix
        dest = upload_dir / f"{stem}_{counter}{suffix}"
        counter += 1
    dest.write_bytes(content)

    content_type = file.content_type or ""
    is_image = content_type.startswith("image/")

    result = {
        "filename": dest.name,
        "path": str(dest),
        "size": len(content),
        "content_type": content_type,
        "is_image": is_image,
        "url": f"/api/files/serve/{dest.name}",
        "download_url": f"/api/files/download/{dest.name}",
    }
    if is_image:
        b64 = base64.b64encode(content).decode("utf-8")
        result["base64"] = f"data:{content_type};base64,{b64}"
    return result


# ===================================================================
#  API: File Serve / Download
# ===================================================================

@app.get("/api/files/serve/{filename:path}")
async def serve_file(filename: str):
    """在浏览器中显示文件（图片内联展示等）。"""
    upload_dir = CBHCLI_DIR / "web_uploads"
    # 安全：禁止路径穿越
    safe_name = filename.replace("..", "").replace("/", "_").replace("\\", "_")
    fp = upload_dir / safe_name
    if not fp.exists() or not fp.is_file():
        raise HTTPException(404, "文件不存在")
    return FileResponse(str(fp), filename=safe_name)


@app.get("/api/files/download/{filename:path}")
async def download_file(filename: str):
    """下载文件（触发浏览器下载）。"""
    upload_dir = CBHCLI_DIR / "web_uploads"
    safe_name = filename.replace("..", "").replace("/", "_").replace("\\", "_")
    fp = upload_dir / safe_name
    if not fp.exists() or not fp.is_file():
        raise HTTPException(404, "文件不存在")
    return FileResponse(str(fp), filename=safe_name,
                        media_type="application/octet-stream")


@app.get("/api/files/serve_path")
async def serve_file_by_path(path: str):
    """按绝对路径提供文件（图片内联显示等），仅限存在的常规文件。"""
    fp = Path(path)
    if not fp.exists() or not fp.is_file():
        raise HTTPException(404, "文件不存在")
    # 安全：禁止访问敏感目录
    resolved = str(fp.resolve())
    for forbidden in ("/etc/", "/proc/", "/sys/", "/dev/", "/boot/"):
        if resolved.startswith(forbidden):
            raise HTTPException(403, "禁止访问该目录")
    return FileResponse(resolved, filename=fp.name)


@app.get("/api/files/download_path")
async def download_file_by_path(path: str):
    """按绝对路径下载文件。"""
    fp = Path(path)
    if not fp.exists() or not fp.is_file():
        raise HTTPException(404, "文件不存在")
    resolved = str(fp.resolve())
    for forbidden in ("/etc/", "/proc/", "/sys/", "/dev/", "/boot/"):
        if resolved.startswith(forbidden):
            raise HTTPException(403, "禁止访问该目录")
    return FileResponse(resolved, filename=fp.name,
                        media_type="application/octet-stream")


# ===================================================================
#  Static Files (Frontend)
# ===================================================================

STATIC_DIR = Path(__file__).parent / "static"


def setup_static():
    if STATIC_DIR.exists():
        app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")


# ===================================================================
#  Server Start
# ===================================================================

def _get_lan_ips() -> list[str]:
    """获取本机所有内网 IPv4 地址（纯标准库实现）。

    1. UDP 连接外网地址获取主出口 IP（不实际发送数据包）
    2. hostname 解析兜底，枚举全部非回环地址
    """
    import socket

    ips: set[str] = set()

    # 主出口 IP（UDP 不产生真实流量）
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            if not ip.startswith("127."):
                ips.add(ip)
    except Exception:
        pass

    # hostname 解析枚举兜底
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if not ip.startswith("127."):
                ips.add(ip)
    except Exception:
        pass

    # 排序：192.168 优先，其次 10.x，再次其他
    def _sort_key(ip: str):
        if ip.startswith("192.168."):
            return (0, ip)
        if ip.startswith("10."):
            return (1, ip)
        if ip.startswith("172."):
            return (2, ip)
        return (3, ip)

    return sorted(ips, key=_sort_key)


def run_server(port: int = 18888, host: str = "0.0.0.0"):
    import uvicorn

    setup_static()

    from cbhcli_pkg import __version__
    print(f"CBHCLI Web v{__version__}")

    # 展示所有可访问地址（localhost + 内网 IP）
    if host in ("0.0.0.0", "::", ""):
        print(f"  ➜ Local:   http://localhost:{port}")
        lan_ips = _get_lan_ips()
        for i, ip in enumerate(lan_ips):
            prefix = "  ➜ Network: " if i == 0 else "             "
            print(f"{prefix}http://{ip}:{port}")
        if not lan_ips:
            print(f"  ➜ Network: （未检测到内网 IP）")
    else:
        print(f"  ➜ Server:  http://{host}:{port}")

    print(f"Config dir: {CBHCLI_DIR}")
    print("Press Ctrl+C to stop\n")

    import webbrowser
    webbrowser.open(f"http://localhost:{port}")

    uvicorn.run(app, host=host, port=port, log_level="warning")
