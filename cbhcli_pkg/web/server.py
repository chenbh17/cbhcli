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
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Request, UploadFile, File, Form
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
from cbhcli_pkg.context.compressor import ContextCompressor


# ===================================================================
#  FastAPI App
# ===================================================================

app = FastAPI(title="CBHCLI Web", version="5.0.3")

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
# MCP 管理器缓存（管理 API 专用，带独立注册表）: agent_name -> MCPManager
_mcp_managers: dict[str, MCPManager] = {}


def get_config() -> GlobalConfig:
    global _global_config
    if _global_config is None:
        _global_config = GlobalConfig()
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
    "memory_search", "knowledge_base", "delegate_task",
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


def _build_tool_registry(agent_name: str, app_proxy: _WebAgentContext) -> ToolRegistry:
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

        # asyncio 原语（在首次使用时绑定到运行中的事件循环）
        self.respond_queue: Optional[asyncio.Queue] = None
        self.lock: Optional[asyncio.Lock] = None

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
        cs.context_compressor = ContextCompressor(cs.llm_client, cs.token_counter)
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

        # SessionStart 钩子（输出暂存，首次聊天时通过 SSE 下发）
        if cs.hook_manager and cs.hook_manager.has_hooks("SessionStart"):
            try:
                decision = cs.hook_manager.run_simple(
                    "SessionStart", session_id=cs.session.id)
                cs.hook_start_outputs = decision.outputs
            except Exception:
                pass

        return cs

    def _rebuild_tools(self):
        """重建工具注册表和 MCP 管理器（MCP 配置变更后同步调用）。"""
        self.tool_registry = _build_tool_registry(self.agent_name, self.app_proxy)
        self.app_proxy.tool_executor = ToolExecutor(self.tool_registry)

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
        """等待前端应答，支持中断。返回 None 表示超时或被中断。"""
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

    def export_messages(self) -> list[dict]:
        """导出为前端可恢复的展示结构（assistant 消息聚合 tool 结果）。"""
        result = []
        for m in self.session.messages:
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


def _get_or_create_session(agent_name: str, model_name: str) -> WebChatSession:
    key = _get_session_key(agent_name, model_name)
    cs = _chat_sessions.get(key)
    if cs is None:
        cs = WebChatSession.create(agent_name, model_name)
        _chat_sessions[key] = cs
    return cs


def _sync_agent_tool_change(agent_name: str, full_rebuild: bool = False):
    """工具开关/MCP 变更后同步到该 Agent 的所有活跃会话。"""
    for cs in _chat_sessions.values():
        if cs.agent_name != agent_name:
            continue
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


class ChatRespondRequest(BaseModel):
    agent_name: str
    model_name: str
    response: str


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
#  API: Chat (SSE Streaming + ReAct Loop)
# ===================================================================

def _sse(data: dict) -> str:
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


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
            yield _sse({"type": "fallback",
                        "content": f"主模型调用失败，切换到备用模型 '{fb_name}'..."})
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


async def _react_loop(cs: WebChatSession):
    """完整 ReAct 循环（与 CLI ai_handler 对齐），产生 SSE 事件。"""
    failure_counts: dict[str, int] = {}
    openai_tools = cs.tool_registry.get_openai_tools()
    stream_kwargs = {"temperature": API_TEMPERATURE}
    if openai_tools:
        stream_kwargs["tools"] = openai_tools
        stream_kwargs["tool_choice"] = "auto"

    # Harness：死循环检测器（每个用户请求独立）+ 权限引擎
    loop_tracker = ToolCallTracker()
    loop_aborted = False
    engine = cs.permission_engine or get_permission_engine()

    for round_idx in range(MAX_TOOL_ROUNDS):
        if cs.abort:
            yield _sse({"type": "aborted"})
            return

        # ---- ReAct 循环内自动压缩 ----
        if cs.auto_compress and cs.context_compressor and cs.context_window:
            total_tokens = cs.session.get_total_tokens(cs.token_counter)
            cs.context_window.update(total_tokens)
            if cs.context_window.needs_compression():
                yield _sse({"type": "compressing",
                            "content": f"上下文接近上限 ({cs.context_window.get_status_text()})，正在自动压缩..."})
                try:
                    success = await asyncio.to_thread(
                        cs.context_compressor.compress,
                        cs.session, cs.context_window.trigger_threshold())
                except Exception:
                    success = False
                if success:
                    new_tokens = cs.session.get_total_tokens(cs.token_counter)
                    cs.context_window.update(new_tokens)
                    yield _sse({"type": "compressed",
                                "content": f"上下文已压缩 ({cs.context_window.get_status_text()})"})
                else:
                    yield _sse({"type": "compress_failed", "content": "压缩失败，继续执行"})

        messages = cs.session.get_context_messages()

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
        for tc in valid_calls:
            if cs.abort:
                yield _sse({"type": "aborted"})
                return

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
            try:
                result = await asyncio.to_thread(
                    cs.tool_registry.execute, tool_name, **tool_args)
            except Exception as e:
                result = ToolResult(success=False, output="", error=str(e))
            result.duration_ms = int((_time.monotonic() - _t0) * 1000)

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

            # 工具结果携带图片（image 工具直发模式）：追加带图用户消息，
            # 使支持视觉的主模型直接在当前会话中查看图片（与 CLI 一致）
            if result.success and getattr(result, "images", None):
                vision_prompt = (result.metadata or {}).get("vision_prompt", "")
                note = f"[image 工具传入 {len(result.images)} 张图片]"
                if vision_prompt:
                    note += f" 识别需求: {vision_prompt}"
                cs.session.add_message("user", note, images=result.images)

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
    """SSE 流式聊天端点（完整 ReAct 工具执行循环）。"""
    cs = _get_or_create_session(req.agent_name, req.model_name)

    if cs.lock.locked():
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

    async def event_stream():
        async with cs.lock:
            try:
                # SessionStart 钩子输出（会话创建时收集，首次聊天时下发）
                if cs.hook_start_outputs:
                    for line in cs.hook_start_outputs:
                        yield _sse({"type": "hook_output",
                                    "event": "SessionStart", "content": line})
                    cs.hook_start_outputs = []

                async for ev in _react_loop(cs):
                    yield ev

                # Stop 钩子：AI 回复完成后触发（与 CLI 一致）
                if cs.hook_manager and cs.hook_manager.has_hooks("Stop"):
                    try:
                        decision = await asyncio.to_thread(
                            cs.hook_manager.run_simple, "Stop",
                            session_id=cs.session.id)
                        for line in decision.outputs:
                            yield _sse({"type": "hook_output",
                                        "event": "Stop", "content": line})
                    except Exception:
                        pass
            except Exception as e:
                yield _sse({"type": "error", "content": str(e)})
                yield _sse({"type": "done", "usage": cs.usage_stats()})

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.post("/api/chat/respond")
async def chat_respond(req: ChatRespondRequest):
    """工具确认 / ask_user 应答。"""
    key = _get_session_key(req.agent_name, req.model_name)
    cs = _chat_sessions.get(key)
    if cs and cs.respond_queue is not None:
        await cs.respond_queue.put(req.response)
        return {"message": "应答已接收"}
    return {"message": "无待处理操作"}


@app.post("/api/chat/reset")
async def reset_chat(req: Request):
    """新建会话（自动保存当前会话到历史）。"""
    body = await req.json()
    agent_name = body.get("agent_name", "")
    model_name = body.get("model_name", "")
    key = _get_session_key(agent_name, model_name)

    cs = _chat_sessions.pop(key, None)
    if cs:
        cs.abort = True
        if cs.session and len(cs.session.messages) > 1:
            agent_config = _get_agent_config(agent_name)
            if agent_config:
                history_mgr = SessionHistoryManager(agent_config.workspace_path)
                try:
                    history_mgr.save_session(
                        cs.session.get_context_messages(), cs.session.id)
                except Exception:
                    pass

    # 释放 python 会话（含 cbhpacks 工具缓存），与 CLI /new 行为一致
    remove_python_session("default")
    return {"message": "会话已重置"}


@app.post("/api/chat/switch_model")
async def chat_switch_model(req: Request):
    """原地切换模型，保留当前会话全部内容（对齐 CLI /model use → switch_model）。

    仅替换 LLM 客户端及关联组件（token计数/压缩器/上下文窗口限制），
    会话消息原样保留，系统提示原地更新（模型名称、视觉能力描述可能变化）。
    会话在 _chat_sessions 中从旧 (agent, model) 键迁移到新键。
    """
    body = await req.json()
    agent_name = body.get("agent_name", "")
    old_model = body.get("old_model", "")
    new_model = body.get("new_model", "")

    if not agent_name or not new_model:
        raise HTTPException(400, "缺少 agent_name / new_model")

    config = get_config()
    model_config = config.get_model(new_model)
    if not model_config:
        raise HTTPException(404, f"模型 '{new_model}' 不存在")
    config.set_last_selected_model(new_model)

    old_key = _get_session_key(agent_name, old_model or new_model)
    new_key = _get_session_key(agent_name, new_model)

    cs = _chat_sessions.get(old_key)
    if cs is None or old_key == new_key:
        # 无活动会话（或新旧模型相同），仅更新选择，无需迁移
        return {"switched": False, "message": f"已选择模型 '{new_model}'"}

    # 新键位若已有旧会话：保存到历史后让位（与 chat_load 行为一致）
    existing = _chat_sessions.pop(new_key, None)
    if existing is not None and existing is not cs:
        existing.abort = True
        if existing.session and len(existing.session.messages) > 1:
            try:
                agent_config = _get_agent_config(agent_name)
                if agent_config:
                    SessionHistoryManager(agent_config.workspace_path).save_session(
                        existing.session.get_context_messages(), existing.session.id)
            except Exception:
                pass

    # 原地替换模型组件（会话消息原样保留）
    cs.llm_client = LLMClient(model_config)
    cs.token_counter = get_token_counter(model_config.get("model"))
    cs.context_compressor = ContextCompressor(cs.llm_client, cs.token_counter)
    if cs.context_window:
        cs.context_window.model_limit = cs.llm_client.context_limit
    if cs.app_proxy:
        cs.app_proxy.llm_client = cs.llm_client
        cs.app_proxy.token_counter = cs.token_counter
        cs.app_proxy.context_compressor = cs.context_compressor
    cs.model_name = new_model
    cs.session_key = new_key
    cs._rebuild_system_prompt()  # 原地更新首条 system 消息

    # 会话键迁移: (agent, old_model) → (agent, new_model)
    _chat_sessions.pop(old_key, None)
    _chat_sessions[new_key] = cs

    return {"switched": True,
            "message": f"已切换到模型 '{new_model}'（会话及上下文已保留）",
            "usage": cs.usage_stats()}


@app.post("/api/chat/abort")
async def chat_abort(req: Request):
    """中断当前流式响应。"""
    body = await req.json()
    agent_name = body.get("agent_name", "")
    model_name = body.get("model_name", "")
    key = _get_session_key(agent_name, model_name)
    cs = _chat_sessions.get(key)
    if cs:
        # wait_response 每 0.5s 轮询一次 abort 标志，无需唤醒队列
        # （向队列投放空串会被误判为用户确认，导致中断失效继续执行工具）
        cs.abort = True
    return {"message": "已请求中断"}


@app.get("/api/chat/status")
def chat_status(agent_name: str, model_name: str):
    """会话状态：token 精确统计 + 上下文占比。"""
    key = _get_session_key(agent_name, model_name)
    cs = _chat_sessions.get(key)
    if not cs:
        return {
            "active": False, "message_count": 0, "token_estimate": 0,
            "ctx_percentage": 0.0, "model_limit": 0, "remaining_tokens": 0,
            "tool_call_count": 0, "cwd": os.getcwd(),
        }
    stats = cs.usage_stats()
    stats.update({"active": True, "cwd": os.getcwd(),
                  "busy": bool(cs.lock and cs.lock.locked())})
    return stats


@app.get("/api/chat/messages")
def chat_messages(agent_name: str, model_name: str):
    """导出当前会话消息（前端刷新后恢复对话）。"""
    key = _get_session_key(agent_name, model_name)
    cs = _chat_sessions.get(key)
    if not cs:
        return {"messages": []}
    return {"messages": cs.export_messages()}


@app.post("/api/chat/load")
async def chat_load(req: Request):
    """加载历史会话为当前会话（完整重建压缩组件，修复旧版缺陷）。"""
    body = await req.json()
    agent_name = body.get("agent_name", "")
    model_name = body.get("model_name", "")
    filename = body.get("filename", "")

    if not agent_name or not model_name or not filename:
        raise HTTPException(400, "缺少 agent_name / model_name / filename")

    agent_config = _get_agent_config(agent_name)
    if not agent_config:
        raise HTTPException(404, f"Agent '{agent_name}' 不存在")

    history_mgr = SessionHistoryManager(agent_config.workspace_path)
    hist_messages = history_mgr.load_session(filename)
    if hist_messages is None:
        raise HTTPException(404, "会话不存在")

    # 先保存并移除旧会话
    key = _get_session_key(agent_name, model_name)
    old = _chat_sessions.pop(key, None)
    if old and old.session and len(old.session.messages) > 1:
        try:
            history_mgr.save_session(old.session.get_context_messages(), old.session.id)
        except Exception:
            pass

    # 全新会话（含全部组件），再注入历史消息
    cs = WebChatSession.create(agent_name, model_name)
    for msg in hist_messages:
        role = msg.get("role", "")
        if role == "system":
            continue  # 保留新建系统提示（含最新 skills/tools.md）
        cs.session.add_message(
            role, msg.get("content", "") or "",
            tool_call_id=msg.get("tool_call_id"),
            tool_calls=msg.get("tool_calls"),
            reasoning_content=msg.get("reasoning_content"),
        )
    _chat_sessions[key] = cs

    return {"message": "会话已加载",
            "messages": cs.export_messages(),
            "usage": cs.usage_stats()}


@app.post("/api/chat/compress")
async def chat_compress(req: Request):
    """手动压缩上下文（对应 CLI /comp）。"""
    body = await req.json()
    agent_name = body.get("agent_name", "")
    model_name = body.get("model_name", "")
    key = _get_session_key(agent_name, model_name)
    cs = _chat_sessions.get(key)
    if not cs:
        raise HTTPException(400, "当前没有活动会话")
    if not cs.context_compressor:
        raise HTTPException(400, "压缩组件未初始化")

    before = cs.session.get_total_tokens(cs.token_counter)
    instructions = body.get("instructions", "") or None
    try:
        success = await asyncio.to_thread(
            cs.context_compressor.compress,
            cs.session, cs.context_window.trigger_threshold(),
            instructions)
    except Exception as e:
        raise HTTPException(500, f"压缩失败: {e}")

    if not success:
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
