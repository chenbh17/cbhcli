"""CBHCLI Web Server - FastAPI backend"""
import json
import asyncio
import threading
import os
import uuid
import base64
import shutil
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Request, UploadFile, File, Form
from fastapi.staticfiles import StaticFiles
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from cbhcli_pkg.config.global_config import GlobalConfig, CBHCLI_DIR
from cbhcli_pkg.core.agent import AgentManager, AgentConfig
from cbhcli_pkg.core.session_history import SessionHistoryManager
from cbhcli_pkg.core.model import LLMClient
from cbhcli_pkg.core.session import Session
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
from cbhcli_pkg.tools.python_tool import PythonTool
from cbhcli_pkg.tools.skills_create import SkillsCreateTool
from cbhcli_pkg.core.mcp_manager import MCPManager
from cbhcli_pkg.core.skill_manager import SkillManager
from cbhcli_pkg.core.constants import MAX_TOOL_ROUNDS, MAX_TOOL_OUTPUT_LENGTH, API_TEMPERATURE
from cbhcli_pkg.core.embedding_client import EmbeddingClient
from cbhcli_pkg.core.rerank_client import RerankClient
from cbhcli_pkg.core.subagent import SubAgentScheduler
from cbhcli_pkg.core.tool_executor import ToolExecutor
from cbhcli_pkg.vector.store import VectorStore
from cbhcli_pkg.vector.indexer import MemoryIndexer
from cbhcli_pkg.context.token_counter import get_token_counter


# ===================================================================
#  FastAPI App
# ===================================================================

app = FastAPI(title="CBHCLI Web", version="4.7.3")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Global state
_global_config: Optional[GlobalConfig] = None
_agent_manager: Optional[AgentManager] = None
_vector_store: Optional[VectorStore] = None
_memory_indexer: Optional[MemoryIndexer] = None
_embedding_client = None
_rerank_client = None


class _WebAgentContext:
    """Mutable proxy that holds per-session state for tools that need app-level references.

    Used by KnowledgeBaseTool, MemorySearchTool (current_agent_name) and
    DelegateTaskTool (subagent_scheduler, llm_client, tool_executor, token_counter).
    """
    def __init__(self, agent_name: str):
        self.current_agent_name = agent_name
        self.is_web = True
        # Populated after session creation for delegate_task
        self.subagent_scheduler = None
        self.llm_client = None
        self.tool_executor = None
        self.token_counter = None
        # Populated after session creation for skills_create
        self.skill_manager = None


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
    """Initialize vector store, embedding client, rerank client (once, lazily)."""
    global _vector_store, _memory_indexer, _embedding_client, _rerank_client

    if _embedding_client is not None or _vector_store is not None:
        return  # already initialized or attempted

    config = get_config()

    # Embedding client
    embedding_config = config.get_embedding_model()
    if embedding_config and embedding_config.get("apiKey"):
        try:
            _embedding_client = EmbeddingClient(embedding_config)
        except Exception:
            _embedding_client = None

    # Rerank client
    rerank_config = config.get_rerank_model()
    if rerank_config and rerank_config.get("apiKey"):
        try:
            _rerank_client = RerankClient(rerank_config)
        except Exception:
            _rerank_client = None

    # Vector store (requires embedding client)
    if _embedding_client:
        try:
            vector_dir = Path.home() / ".cbhcli" / "vectors"
            _vector_store = VectorStore(vector_dir, embedding_client=_embedding_client)
            _memory_indexer = MemoryIndexer(_vector_store)
        except Exception:
            _vector_store = None
            _memory_indexer = None


# Tools that skip confirmation (read-only / interactive)
_READONLY_TOOLS = {"grep", "glob", "ask_user", "read", "Todo", "memory_search", "knowledge_base", "delegate_task"}


def _get_tool_registry(agent_name: str = "", app_proxy: '_WebAgentContext' = None) -> ToolRegistry:
    """Create and populate a ToolRegistry with standard tools."""
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
    registry.register(PythonTool("default"))

    # Register knowledge_base, memory_search, delegate_task, skills_create tools
    ctx = app_proxy or (_WebAgentContext(agent_name) if agent_name else None)
    registry.register(MemorySearchTool(
        vector_store=_vector_store,
        agent_manager=get_agent_manager(),
        app=ctx,
    ))
    registry.register(KnowledgeBaseTool(
        vector_store=_vector_store,
        agent_manager=get_agent_manager(),
        rerank_client=_rerank_client,
        app=ctx,
    ))
    if ctx:
        registry.register(DelegateTaskTool(ctx))
        registry.register(SkillsCreateTool(ctx))

    return registry


def _get_agent_workspace(agent_name: str) -> Path:
    """Get agent workspace path, raise 404 if not found."""
    manager = get_agent_manager()
    config = manager.load_agent(agent_name)
    if not config:
        raise HTTPException(404, f"Agent '{agent_name}' not found")
    return config.workspace_path


# ===================================================================
#  Pydantic Models
# ===================================================================

class ModelConfig(BaseModel):
    name: str
    apiKey: str
    url: str
    model: str
    context_limit: int = 128000


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


class MCPToolToggle(BaseModel):
    enable: bool


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
        raise HTTPException(400, f"Model '{model.name}' already exists")
    config.add_model(model.model_dump())
    return {"message": f"Model '{model.name}' added"}


# --- Embedding Model (must be before {model_name} routes) ---
@app.put("/api/models/embedding")
def update_embedding_model(model: EmbeddingModelConfig):
    config = get_config()
    config.set_embedding_model(model.model_dump())
    return {"message": "Embedding model updated"}


@app.delete("/api/models/embedding")
def delete_embedding_model():
    config = get_config()
    config.delete_embedding_model()
    return {"message": "Embedding model deleted"}


# --- Rerank Model (must be before {model_name} routes) ---
@app.put("/api/models/rerank")
def update_rerank_model(model: RerankModelConfig):
    config = get_config()
    config.set_rerank_model(model.model_dump())
    return {"message": "Rerank model updated"}


@app.delete("/api/models/rerank")
def delete_rerank_model():
    config = get_config()
    config.delete_rerank_model()
    return {"message": "Rerank model deleted"}


# --- Generic model CRUD (parameterized, must come after specific routes) ---
@app.put("/api/models/{model_name}")
def update_model(model_name: str, model: ModelConfig):
    config = get_config()
    models = config.get_models()
    for i, m in enumerate(models):
        if m.get("name") == model_name:
            models[i] = model.model_dump()
            config.save()
            return {"message": f"Model '{model_name}' updated"}
    raise HTTPException(404, f"Model '{model_name}' not found")


@app.delete("/api/models/{model_name}")
def delete_model(model_name: str):
    config = get_config()
    if config.delete_model(model_name):
        return {"message": f"Model '{model_name}' deleted"}
    raise HTTPException(404, f"Model '{model_name}' not found")


@app.post("/api/models/{model_name}/select")
def select_model(model_name: str):
    config = get_config()
    if not config.get_model(model_name):
        raise HTTPException(404, f"Model '{model_name}' not found")
    config.set_last_selected_model(model_name)
    return {"message": f"Selected model '{model_name}'"}


# ===================================================================
#  API: Agent Management
# ===================================================================

@app.get("/api/agents")
def list_agents():
    manager = get_agent_manager()
    config = get_config()
    agents = manager.list_agents()
    active = config.get_active_agent()
    return {
        "agents": [a.to_dict() for a in agents],
        "active_agent": active,
    }


@app.post("/api/agents")
def create_agent(agent: AgentCreate):
    manager = get_agent_manager()
    if manager.load_agent(agent.name):
        raise HTTPException(400, f"Agent '{agent.name}' already exists")
    config = manager.create_agent(
        name=agent.name,
        description=agent.description,
        primary_model=agent.primary_model,
    )
    return {"message": f"Agent '{agent.name}' created", "agent": config.to_dict()}


@app.get("/api/agents/{agent_name}")
def get_agent(agent_name: str):
    manager = get_agent_manager()
    config = manager.load_agent(agent_name)
    if not config:
        raise HTTPException(404, f"Agent '{agent_name}' not found")

    workspace = config.workspace_path

    files = {}
    for fname in ["soul.md", "tools.md", "memory.md", "usage.md"]:
        fpath = workspace / fname
        if fpath.exists():
            files[fname] = fpath.read_text(encoding="utf-8")

    # Skills summary
    skills = []
    skills_dir = workspace / "skills"
    if skills_dir.exists():
        for d in skills_dir.iterdir():
            if d.is_dir() and (d / "skills.md").exists():
                skills.append({
                    "name": d.name,
                    "content": (d / "skills.md").read_text(encoding="utf-8")[:200],
                })

    # MCP config
    mcp_config_file = workspace / "mcp.json"
    mcp_servers = []
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
        raise HTTPException(404, f"Agent '{agent_name}' not found")

    data = config.to_dict()
    for key, val in update.model_dump(exclude_none=True).items():
        data[key] = val

    new_config = AgentConfig.from_dict(data, config.workspace_path)
    config_file = config.workspace_path / "config.json"
    with open(config_file, "w", encoding="utf-8") as f:
        json.dump(new_config.to_dict(), f, indent=2, ensure_ascii=False)

    return {"message": f"Agent '{agent_name}' updated"}


@app.delete("/api/agents/{agent_name}")
def delete_agent(agent_name: str):
    manager = get_agent_manager()
    if manager.delete_agent(agent_name):
        return {"message": f"Agent '{agent_name}' deleted"}
    raise HTTPException(404, f"Agent '{agent_name}' not found")


@app.post("/api/agents/{agent_name}/select")
def select_agent(agent_name: str):
    manager = get_agent_manager()
    config = get_config()
    if not manager.load_agent(agent_name):
        raise HTTPException(404, f"Agent '{agent_name}' not found")
    config.set_active_agent(agent_name)
    return {"message": f"Switched to Agent '{agent_name}'"}


@app.put("/api/agents/{agent_name}/files/{filename}")
def update_agent_file(agent_name: str, filename: str, body: FileContent):
    manager = get_agent_manager()
    config = manager.load_agent(agent_name)
    if not config:
        raise HTTPException(404, f"Agent '{agent_name}' not found")

    allowed = {"soul.md", "tools.md", "memory.md", "usage.md"}
    if filename not in allowed:
        raise HTTPException(400, f"Cannot edit file: {filename}")

    fpath = config.workspace_path / filename
    fpath.write_text(body.content, encoding="utf-8")
    return {"message": f"{filename} updated"}


# ===================================================================
#  API: Skills Management
# ===================================================================

@app.get("/api/agents/{agent_name}/skills")
def list_skills(agent_name: str):
    workspace = _get_agent_workspace(agent_name)
    from cbhcli_pkg.core.skill_manager import SkillManager
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
                "prompt_preview": s.prompt[:300] if s.prompt else "",
            }
            for s in skills
        ],
        "active": active_names,
    }


@app.post("/api/agents/{agent_name}/skills/activate")
def activate_skills(agent_name: str, body: SkillActivate):
    workspace = _get_agent_workspace(agent_name)
    from cbhcli_pkg.core.skill_manager import SkillManager
    sm = SkillManager(workspace)
    activated = sm.activate_skills(body.names)
    return {"message": f"Activated {len(activated)} skills", "activated": activated}


@app.post("/api/agents/{agent_name}/skills/{skill_name}/deactivate")
def deactivate_skill(agent_name: str, skill_name: str):
    workspace = _get_agent_workspace(agent_name)
    from cbhcli_pkg.core.skill_manager import SkillManager
    sm = SkillManager(workspace)
    if sm.deactivate_skill(skill_name):
        return {"message": f"Deactivated skill '{skill_name}'"}
    raise HTTPException(404, f"Skill '{skill_name}' not found or not active")


@app.delete("/api/agents/{agent_name}/skills/{skill_name}")
def delete_skill(agent_name: str, skill_name: str):
    workspace = _get_agent_workspace(agent_name)
    from cbhcli_pkg.core.skill_manager import SkillManager
    sm = SkillManager(workspace)
    if sm.remove_skill(skill_name):
        return {"message": f"Deleted skill '{skill_name}'"}
    raise HTTPException(404, f"Skill '{skill_name}' not found")


# ===================================================================
#  API: MCP Management
# ===================================================================

@app.get("/api/agents/{agent_name}/mcp")
def list_mcp_servers(agent_name: str):
    workspace = _get_agent_workspace(agent_name)
    mcp_config_file = workspace / "mcp.json"
    servers = []
    if mcp_config_file.exists():
        try:
            data = json.loads(mcp_config_file.read_text(encoding="utf-8"))
            servers = data.get("servers", [])
        except Exception:
            pass
    return {"servers": servers}


@app.post("/api/agents/{agent_name}/mcp")
def add_mcp_server(agent_name: str, body: MCPServerAdd):
    workspace = _get_agent_workspace(agent_name)
    mcp_config_file = workspace / "mcp.json"
    data = {"servers": []}
    if mcp_config_file.exists():
        try:
            data = json.loads(mcp_config_file.read_text(encoding="utf-8"))
        except Exception:
            pass

    servers = data.get("servers", [])
    if any(s["name"] == body.name for s in servers):
        raise HTTPException(400, f"MCP server '{body.name}' already exists")

    servers.append({
        "name": body.name,
        "url": body.url,
        "headers": body.headers or {},
        "enabled_tools": body.enabled_tools,
    })
    data["servers"] = servers
    mcp_config_file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"message": f"Added MCP server '{body.name}'"}


@app.delete("/api/agents/{agent_name}/mcp/{server_name}")
def remove_mcp_server(agent_name: str, server_name: str):
    workspace = _get_agent_workspace(agent_name)
    mcp_config_file = workspace / "mcp.json"
    if not mcp_config_file.exists():
        raise HTTPException(404, "No MCP config")

    data = json.loads(mcp_config_file.read_text(encoding="utf-8"))
    servers = data.get("servers", [])
    new_servers = [s for s in servers if s["name"] != server_name]
    if len(new_servers) == len(servers):
        raise HTTPException(404, f"MCP server '{server_name}' not found")

    data["servers"] = new_servers
    mcp_config_file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"message": f"Removed MCP server '{server_name}'"}


@app.post("/api/agents/{agent_name}/mcp/{server_name}/refresh")
def refresh_mcp_server(agent_name: str, server_name: str):
    # Config-level refresh: just verify config exists
    workspace = _get_agent_workspace(agent_name)
    mcp_config_file = workspace / "mcp.json"
    if not mcp_config_file.exists():
        raise HTTPException(404, "No MCP config")

    data = json.loads(mcp_config_file.read_text(encoding="utf-8"))
    servers = data.get("servers", [])
    if not any(s["name"] == server_name for s in servers):
        raise HTTPException(404, f"MCP server '{server_name}' not found")

    return {"message": f"MCP server '{server_name}' refresh requested (effective on next CLI session)"}


@app.put("/api/agents/{agent_name}/mcp/{server_name}/tools/{tool_name}")
def toggle_mcp_tool(agent_name: str, server_name: str, tool_name: str, body: MCPToolToggle):
    workspace = _get_agent_workspace(agent_name)
    mcp_config_file = workspace / "mcp.json"
    if not mcp_config_file.exists():
        raise HTTPException(404, "No MCP config")

    data = json.loads(mcp_config_file.read_text(encoding="utf-8"))
    servers = data.get("servers", [])
    server = None
    for s in servers:
        if s["name"] == server_name:
            server = s
            break
    if not server:
        raise HTTPException(404, f"MCP server '{server_name}' not found")

    enabled_tools = server.get("enabled_tools")
    if body.enable:
        if enabled_tools is not None and tool_name not in enabled_tools:
            enabled_tools.append(tool_name)
    else:
        if enabled_tools is None:
            # Switch from "all enabled" to explicit list minus this tool
            server["enabled_tools"] = []  # Frontend must re-populate
        elif tool_name in enabled_tools:
            enabled_tools.remove(tool_name)

    data["servers"] = servers
    mcp_config_file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    action = "enabled" if body.enable else "disabled"
    return {"message": f"Tool '{tool_name}' {action}"}


# ===================================================================
#  API: Knowledge Base Management
# ===================================================================

@app.get("/api/agents/{agent_name}/knowledge")
def list_knowledge(agent_name: str):
    workspace = _get_agent_workspace(agent_name)
    kb_dir = workspace / "knowledge"
    if not kb_dir.exists():
        return {"files": []}
    files = []
    for f in sorted(kb_dir.iterdir()):
        if f.is_file():
            files.append({
                "name": f.name,
                "size": f.stat().st_size,
                "path": str(f),
            })
    return {"files": files}


@app.post("/api/agents/{agent_name}/knowledge")
def add_knowledge_file(agent_name: str, body: KnowledgeAdd):
    _init_vector_store()
    workspace = _get_agent_workspace(agent_name)
    from cbhcli_pkg.core.knowledge_base import KnowledgeBase
    kb = KnowledgeBase(agent_name, vector_store=_vector_store, indexer=_memory_indexer)
    result = kb.add_file(body.file_path)
    if not result.get("success"):
        raise HTTPException(400, result.get("message", "Failed"))
    return result


@app.post("/api/agents/{agent_name}/knowledge/upload")
async def upload_knowledge_file(agent_name: str, file: UploadFile = File(...)):
    """Upload a file from browser to the agent's knowledge base."""
    _init_vector_store()
    workspace = _get_agent_workspace(agent_name)
    if not file.filename:
        raise HTTPException(400, "No file provided")

    content = await file.read()
    max_size = 50 * 1024 * 1024  # 50MB limit
    if len(content) > max_size:
        raise HTTPException(400, "File too large (max 50MB)")

    # Save to a temp location, then use KnowledgeBase.add_file
    upload_dir = CBHCLI_DIR / "web_uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)
    safe_name = file.filename.replace("/", "_").replace("\\", "_")
    temp_path = upload_dir / safe_name
    temp_path.write_bytes(content)

    from cbhcli_pkg.core.knowledge_base import KnowledgeBase
    kb = KnowledgeBase(agent_name, vector_store=_vector_store, indexer=_memory_indexer)
    result = kb.add_file(str(temp_path))

    # Clean up temp file
    try:
        temp_path.unlink()
    except Exception:
        pass

    if not result.get("success"):
        raise HTTPException(400, result.get("message", "Failed"))
    return result


@app.delete("/api/agents/{agent_name}/knowledge/{file_name}")
def remove_knowledge_file(agent_name: str, file_name: str):
    _init_vector_store()
    workspace = _get_agent_workspace(agent_name)
    from cbhcli_pkg.core.knowledge_base import KnowledgeBase
    kb = KnowledgeBase(agent_name, vector_store=_vector_store, indexer=_memory_indexer)
    result = kb.remove_file(file_name)
    if not result.get("success"):
        raise HTTPException(404, result.get("message", "Not found"))
    return result


@app.post("/api/agents/{agent_name}/knowledge/reindex")
def reindex_knowledge(agent_name: str):
    _init_vector_store()
    _get_agent_workspace(agent_name)
    from cbhcli_pkg.core.knowledge_base import KnowledgeBase
    kb = KnowledgeBase(agent_name, vector_store=_vector_store, indexer=_memory_indexer)
    result = kb.reindex_all()
    return result


# ===================================================================
#  API: Session History
# ===================================================================

@app.get("/api/agents/{agent_name}/history")
def list_history(agent_name: str, limit: int = 20):
    manager = get_agent_manager()
    config = manager.load_agent(agent_name)
    if not config:
        raise HTTPException(404, f"Agent '{agent_name}' not found")

    history_mgr = SessionHistoryManager(config.workspace_path)
    sessions = history_mgr.list_sessions(limit)
    return {"sessions": sessions}


@app.get("/api/agents/{agent_name}/history/{filename}")
def get_history(agent_name: str, filename: str):
    manager = get_agent_manager()
    config = manager.load_agent(agent_name)
    if not config:
        raise HTTPException(404, f"Agent '{agent_name}' not found")

    history_mgr = SessionHistoryManager(config.workspace_path)
    messages = history_mgr.load_session(filename)
    if messages is None:
        raise HTTPException(404, "Session not found")
    return {"messages": messages}


@app.delete("/api/agents/{agent_name}/history/{filename}")
def delete_history(agent_name: str, filename: str):
    manager = get_agent_manager()
    config = manager.load_agent(agent_name)
    if not config:
        raise HTTPException(404, f"Agent '{agent_name}' not found")

    history_mgr = SessionHistoryManager(config.workspace_path)
    if history_mgr.delete_session(filename):
        return {"message": "Session deleted"}
    raise HTTPException(404, "Session not found")


# ===================================================================
#  API: Chat (SSE Streaming)
# ===================================================================

_chat_sessions: dict[str, dict] = {}
# Pending action responses: session_key -> asyncio.Queue for user responses
_pending_responses: dict[str, asyncio.Queue] = {}


@app.post("/api/chat")
async def chat(req: ChatRequest):
    """SSE streaming chat endpoint with full ReAct tool execution loop."""
    config = get_config()
    manager = get_agent_manager()

    model_config = config.get_model(req.model_name)
    if not model_config:
        raise HTTPException(400, f"Model '{req.model_name}' not found")

    agent_config = manager.load_agent(req.agent_name)
    if not agent_config:
        raise HTTPException(400, f"Agent '{req.agent_name}' not found")

    session_key = f"{req.agent_name}:{req.model_name}"
    if session_key not in _chat_sessions:
        persona = manager.load_agent_persona(req.agent_name)
        session = Session(agent_name=req.agent_name)
        llm_client = LLMClient(model_config)

        # Create a mutable proxy for tools that need app-level references
        app_proxy = _WebAgentContext(req.agent_name)
        tool_registry = _get_tool_registry(req.agent_name, app_proxy=app_proxy)

        # Populate proxy for delegate_task tool
        app_proxy.llm_client = llm_client
        app_proxy.subagent_scheduler = SubAgentScheduler()
        app_proxy.tool_executor = ToolExecutor(tool_registry)
        app_proxy.token_counter = get_token_counter()

        # Load MCP tools for this agent
        mcp_manager = None
        try:
            mcp_manager = MCPManager(
                req.agent_name, agent_config.workspace_path, tool_registry
            )
        except Exception:
            pass

        # Load Skills prompt for this agent
        active_skills_prompt = ""
        skill_manager = None
        try:
            skill_manager = SkillManager(agent_config.workspace_path)
            active_skills_prompt = skill_manager.build_skills_prompt()
        except Exception:
            pass
        app_proxy.skill_manager = skill_manager

        # Build system prompt with tool descriptions and skills
        tool_descriptions = tool_registry.get_tool_descriptions()
        system_prompt = persona.build_system_prompt(
            tool_descriptions,
            agent_name=req.agent_name,
            model_name=req.model_name,
            active_skills_prompt=active_skills_prompt,
            cwd=os.getcwd(),
        )
        session.add_message("system", system_prompt)

        _chat_sessions[session_key] = {
            "session": session,
            "llm_client": llm_client,
            "tool_registry": tool_registry,
            "mcp_manager": mcp_manager,
            "skill_manager": skill_manager,
            "no_more_confirmations": False,
        }

    chat_data = _chat_sessions[session_key]
    session = chat_data["session"]
    llm_client = chat_data["llm_client"]
    tool_registry = chat_data["tool_registry"]

    session.add_message("user", req.message)
    # Reset abort flag
    chat_data["abort"] = False

    # Create response queue for tool confirmations
    if session_key not in _pending_responses:
        _pending_responses[session_key] = asyncio.Queue()

    async def event_stream():
        openai_tools = tool_registry.get_openai_tools()
        stream_kwargs = {"temperature": API_TEMPERATURE}
        if openai_tools:
            stream_kwargs["tools"] = openai_tools
            stream_kwargs["tool_choice"] = "auto"

        loop = asyncio.get_event_loop()

        for round_idx in range(MAX_TOOL_ROUNDS):
            if chat_data.get("abort"):
                yield _sse({"type": "aborted"})
                break

            messages = session.get_context_messages()
            ai_response = ""
            reasoning_buffer = ""
            tc_buffer = {}  # index -> {id, name, arguments}

            try:
                chunks_queue = asyncio.Queue()

                def stream_worker():
                    try:
                        for chunk_type, content in llm_client.chat_stream(
                            messages, **stream_kwargs
                        ):
                            if chat_data.get("abort"):
                                asyncio.run_coroutine_threadsafe(
                                    chunks_queue.put(("aborted", "")), loop
                                )
                                return
                            asyncio.run_coroutine_threadsafe(
                                chunks_queue.put((chunk_type, content)), loop
                            )
                        asyncio.run_coroutine_threadsafe(
                            chunks_queue.put(None), loop
                        )
                    except Exception as e:
                        asyncio.run_coroutine_threadsafe(
                            chunks_queue.put(("error", str(e))), loop
                        )

                thread = threading.Thread(target=stream_worker, daemon=True)
                thread.start()

                while True:
                    item = await chunks_queue.get()
                    if item is None:
                        break
                    chunk_type, content = item

                    if chunk_type == "error":
                        yield _sse({"type": "error", "content": content})
                        session.add_message("assistant", ai_response or "Error",
                                            reasoning_content=reasoning_buffer or None)
                        yield _sse({"type": "done"})
                        return
                    elif chunk_type == "aborted":
                        yield _sse({"type": "aborted"})
                        yield _sse({"type": "done"})
                        return
                    elif chunk_type == "reasoning":
                        reasoning_buffer += content
                        yield _sse({"type": "reasoning", "content": content})
                    elif chunk_type == "content":
                        ai_response += content
                        yield _sse({"type": "content", "content": content})
                    elif chunk_type == "tool_calls":
                        # Collect incremental tool_calls data
                        try:
                            tool_calls_data = json.loads(content)
                            for tc in tool_calls_data:
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

            except Exception as e:
                yield _sse({"type": "error", "content": str(e)})
                yield _sse({"type": "done"})
                return

            # Build structured tool_calls from buffer
            tool_calls = []
            if tc_buffer:
                for idx in sorted(tc_buffer.keys()):
                    tc = tc_buffer[idx]
                    if tc["name"]:
                        tc_id = tc["id"] or f"call_{uuid.uuid4().hex[:8]}"
                        try:
                            args = json.loads(tc["arguments"]) if tc["arguments"] else {}
                        except json.JSONDecodeError:
                            args = {}
                        tool_calls.append({
                            "id": tc_id,
                            "name": tc["name"],
                            "arguments": args,
                        })

            if not tool_calls:
                # No tool calls -> save response, done
                if ai_response:
                    session.add_message("assistant", ai_response,
                                        reasoning_content=reasoning_buffer or None)
                yield _sse({"type": "done"})
                return

            # === Tool calls detected ===
            # Save assistant message with tool_calls to session
            openai_tool_calls = []
            for tc in tool_calls:
                openai_tool_calls.append({
                    "id": tc["id"],
                    "type": "function",
                    "function": {
                        "name": tc["name"],
                        "arguments": json.dumps(tc["arguments"])
                    }
                })
            session.add_message("assistant", ai_response or "",
                                reasoning_content=reasoning_buffer or None,
                                tool_calls=openai_tool_calls)

            # Execute each tool call
            for tc in tool_calls:
                tool_name = tc["name"]
                tool_args = tc["arguments"]
                tool_id = tc["id"]

                # Resolve tool name via fuzzy matching
                tool_obj = tool_registry.fuzzy_get(tool_name)
                resolved_name = tool_obj.name if tool_obj else tool_name

                # Special handling for ask_user (needs web interaction, not stdin)
                if resolved_name == "ask_user":
                    question = tool_args.get("question", "AI needs your input")
                    options = tool_args.get("options", [])
                    yield _sse({
                        "type": "ask_user",
                        "question": question,
                        "options": options,
                        "tool_id": tool_id,
                    })
                    # Wait for user answer
                    try:
                        resp_queue = _pending_responses[session_key]
                        while not resp_queue.empty():
                            try: resp_queue.get_nowait()
                            except asyncio.QueueEmpty: break
                        user_answer = await asyncio.wait_for(
                            resp_queue.get(), timeout=300
                        )
                    except asyncio.TimeoutError:
                        user_answer = "用户未回答"
                    tool_output = f"用户回答: {user_answer}"
                    session.add_message("tool", tool_output, tool_call_id=tool_id)
                    yield _sse({
                        "type": "tool_result",
                        "tool_name": resolved_name,
                        "tool_id": tool_id,
                        "success": True,
                        "preview": tool_output,
                    })
                    continue

                # Emit tool_confirm event to frontend
                yield _sse({
                    "type": "tool_confirm",
                    "tool_name": resolved_name,
                    "tool_args": _tool_preview(resolved_name, tool_args),
                    "tool_id": tool_id,
                })

                # Decide if we need user confirmation
                needs_confirm = (
                    resolved_name not in _READONLY_TOOLS
                    and not chat_data.get("no_more_confirmations")
                )

                user_response = "y"
                if needs_confirm:
                    # Wait for user response via /api/chat/respond
                    try:
                        resp_queue = _pending_responses[session_key]
                        # Drain stale responses
                        while not resp_queue.empty():
                            try:
                                resp_queue.get_nowait()
                            except asyncio.QueueEmpty:
                                break
                        user_response = await asyncio.wait_for(
                            resp_queue.get(), timeout=300  # 5 min timeout
                        )
                    except asyncio.TimeoutError:
                        user_response = "n"
                else:
                    # Auto-confirm for read-only tools
                    yield _sse({
                        "type": "tool_auto_confirmed",
                        "tool_name": resolved_name,
                        "tool_id": tool_id,
                    })

                user_response = user_response.strip().lower()
                if user_response == "all":
                    chat_data["no_more_confirmations"] = True
                    user_response = "y"

                if user_response in ("n", "no"):
                    # User rejected
                    yield _sse({
                        "type": "tool_rejected",
                        "tool_name": resolved_name,
                        "tool_id": tool_id,
                    })
                    tool_output = "用户取消了执行"
                    session.add_message("tool", tool_output, tool_call_id=tool_id)
                    continue

                # Execute tool
                yield _sse({
                    "type": "tool_executing",
                    "tool_name": resolved_name,
                    "tool_id": tool_id,
                })

                try:
                    result = tool_registry.execute(resolved_name, **tool_args)
                    if result.success:
                        tool_output = result.output[:MAX_TOOL_OUTPUT_LENGTH] if result.output else ""
                    else:
                        tool_output = f"错误: {result.error}"
                except Exception as e:
                    tool_output = f"工具执行异常: {str(e)}"
                    result = ToolResult(success=False, output="", error=str(e))

                # Send result preview to frontend
                preview = tool_output[:7500] if len(tool_output) > 7500 else tool_output
                
                # Build structured preview for special tools
                preview_data = None
                if resolved_name == "edit" and result.success:
                    preview_data = {
                        "type": "edit",
                        "file_path": tool_args.get("file_path", ""),
                        "old_str": tool_args.get("old_str", ""),
                        "new_str": tool_args.get("new_str", ""),
                    }
                elif resolved_name == "write" and result.success:
                    preview_data = {
                        "type": "write",
                        "file_path": tool_args.get("file_path", ""),
                        "content": tool_args.get("content", ""),
                    }
                elif resolved_name == "python":
                    preview_data = {
                        "type": "python",
                        "code": tool_args.get("code", ""),
                        "output": preview,
                        "success": result.success if hasattr(result, 'success') else True,
                    }
                
                sse_data = {
                    "type": "tool_result",
                    "tool_name": resolved_name,
                    "tool_id": tool_id,
                    "success": result.success if hasattr(result, 'success') else True,
                    "preview": preview,
                }
                if preview_data:
                    sse_data["preview_data"] = preview_data
                yield _sse(sse_data)

                # Add tool result to session for next LLM round
                session.add_message("tool", tool_output, tool_call_id=tool_id)

            # Continue the ReAct loop - next round will call LLM with tool results

        # Max rounds reached
        yield _sse({"type": "content", "content": "\n\n[达到最大工具调用轮数]"})
        yield _sse({"type": "done"})

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


def _sse(data: dict) -> str:
    """Format a dict as an SSE data line."""
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


def _tool_preview(tool_name: str, arguments: dict) -> str:
    """Get a short preview of tool arguments for display."""
    if tool_name == "terminal":
        cmd = arguments.get("command", "") or arguments.get("cmd", "")
        return cmd[:100] if cmd else ""
    elif tool_name in ("read", "write", "edit"):
        return arguments.get("path", arguments.get("file_path", ""))
    elif tool_name == "grep":
        pattern = arguments.get("pattern", "")
        path = arguments.get("path", ".")
        return f"/{pattern}/ in {path}"
    elif tool_name == "glob":
        return arguments.get("pattern", "")
    elif tool_name == "ask_user":
        return arguments.get("question", "")[:80]
    elif tool_name == "Todo":
        return "Todo list update"
    elif tool_name == "delegate_task":
        return arguments.get("task", "")[:100]
    elif tool_name == "python":
        code = arguments.get("code", "")
        return code[:100] if code else ""
    elif tool_name == "skills_create":
        return arguments.get("skill_name", "")
    return json.dumps(arguments, ensure_ascii=False)[:100]


@app.post("/api/chat/reset")
async def reset_chat(req: Request):
    body = await req.json()
    agent_name = body.get("agent_name", "")
    model_name = body.get("model_name", "")
    session_key = f"{agent_name}:{model_name}"

    if session_key in _chat_sessions:
        chat_data = _chat_sessions[session_key]
        session = chat_data["session"]
        if len(session.messages) > 1:
            manager = get_agent_manager()
            agent_config = manager.load_agent(agent_name)
            if agent_config:
                history_mgr = SessionHistoryManager(agent_config.workspace_path)
                history_mgr.save_session(
                    session.get_context_messages(), session.id
                )
        del _chat_sessions[session_key]

    return {"message": "Session reset"}


@app.post("/api/chat/respond")
async def chat_respond(req: ChatRespondRequest):
    """Endpoint for tool confirmation / ask_user / password responses."""
    session_key = f"{req.agent_name}:{req.model_name}"
    if session_key in _pending_responses:
        await _pending_responses[session_key].put(req.response)
        return {"message": "Response received"}
    return {"message": "No pending action"}


@app.get("/api/chat/status")
def chat_status(agent_name: str, model_name: str):
    """Get current chat session status: message count, token estimate, ctx percentage."""
    session_key = f"{agent_name}:{model_name}"
    if session_key not in _chat_sessions:
        return {
            "active": False,
            "message_count": 0,
            "token_estimate": 0,
            "ctx_percentage": 0.0,
            "model_limit": 0,
            "cwd": os.getcwd(),
        }
    chat_data = _chat_sessions[session_key]
    session = chat_data["session"]
    llm_client = chat_data["llm_client"]

    # Simple token estimation: ~4 chars per token
    total_chars = sum(len(m.content or "") for m in session.messages)
    token_estimate = total_chars // 4
    model_limit = llm_client.context_limit if hasattr(llm_client, 'context_limit') else 128000
    if model_limit <= 0:
        model_limit = 128000
    ctx_pct = min(100.0, (token_estimate / model_limit) * 100)

    return {
        "active": True,
        "message_count": len(session.messages),
        "token_estimate": token_estimate,
        "ctx_percentage": round(ctx_pct, 1),
        "model_limit": model_limit,
        "cwd": os.getcwd(),
    }


@app.post("/api/chat/abort")
async def chat_abort(req: Request):
    """Abort the current streaming response."""
    body = await req.json()
    agent_name = body.get("agent_name", "")
    model_name = body.get("model_name", "")
    session_key = f"{agent_name}:{model_name}"
    # Set abort flag
    if session_key in _chat_sessions:
        _chat_sessions[session_key]["abort"] = True
    return {"message": "Abort requested"}


@app.post("/api/chat/load")
async def chat_load(req: Request):
    """Load a history session into the current chat session."""
    body = await req.json()
    agent_name = body.get("agent_name", "")
    model_name = body.get("model_name", "")
    filename = body.get("filename", "")

    if not agent_name or not model_name or not filename:
        raise HTTPException(400, "Missing agent_name, model_name, or filename")

    manager = get_agent_manager()
    agent_config = manager.load_agent(agent_name)
    if not agent_config:
        raise HTTPException(404, f"Agent '{agent_name}' not found")

    config = get_config()
    model_config = config.get_model(model_name)
    if not model_config:
        raise HTTPException(400, f"Model '{model_name}' not found")

    # Load history messages
    history_mgr = SessionHistoryManager(agent_config.workspace_path)
    hist_messages = history_mgr.load_session(filename)
    if hist_messages is None:
        raise HTTPException(404, "Session not found")

    session_key = f"{agent_name}:{model_name}"

    # Create or reset session
    session = Session(agent_name=agent_name)
    llm_client = LLMClient(model_config)

    # Create a mutable proxy for tools that need app-level references
    app_proxy = _WebAgentContext(agent_name)
    tool_registry = _get_tool_registry(agent_name, app_proxy=app_proxy)

    # Populate proxy for delegate_task tool
    app_proxy.llm_client = llm_client
    app_proxy.subagent_scheduler = SubAgentScheduler()
    app_proxy.tool_executor = ToolExecutor(tool_registry)
    app_proxy.token_counter = get_token_counter()

    # Load MCP tools
    mcp_manager = None
    try:
        mcp_manager = MCPManager(agent_name, agent_config.workspace_path, tool_registry)
    except Exception:
        pass

    # Load Skills prompt
    active_skills_prompt = ""
    skill_manager = None
    try:
        skill_manager = SkillManager(agent_config.workspace_path)
        active_skills_prompt = skill_manager.build_skills_prompt()
    except Exception:
        pass
    app_proxy.skill_manager = skill_manager

    # Restore messages from history into session
    for msg in hist_messages:
        role = msg.get("role", "")
        content = msg.get("content", "")
        tool_call_id = msg.get("tool_call_id")
        tool_calls = msg.get("tool_calls")
        reasoning_content = msg.get("reasoning_content")

        if role == "system":
            # Rebuild system prompt with current tools/skills instead of old one
            persona = manager.load_agent_persona(agent_name)
            tool_descriptions = tool_registry.get_tool_descriptions()
            system_prompt = persona.build_system_prompt(
                tool_descriptions,
                agent_name=agent_name,
                model_name=model_name,
                active_skills_prompt=active_skills_prompt,
                cwd=os.getcwd(),
            )
            session.add_message("system", system_prompt)
        else:
            session.add_message(
                role, content,
                tool_call_id=tool_call_id,
                tool_calls=tool_calls,
                reasoning_content=reasoning_content,
            )

    _chat_sessions[session_key] = {
        "session": session,
        "llm_client": llm_client,
        "tool_registry": tool_registry,
        "mcp_manager": mcp_manager,
        "skill_manager": skill_manager,
        "no_more_confirmations": False,
    }

    return {"message": "Session loaded", "message_count": len(session.messages)}


@app.post("/api/chat/upload")
async def chat_upload(file: UploadFile = File(...), agent_name: str = Form(...), model_name: str = Form(...)):
    """Upload a file or image to be included in the next chat message."""
    if not file.filename:
        raise HTTPException(400, "No file provided")

    content = await file.read()
    max_size = 10 * 1024 * 1024  # 10MB limit
    if len(content) > max_size:
        raise HTTPException(400, "File too large (max 10MB)")

    # Save to temp upload dir
    upload_dir = CBHCLI_DIR / "web_uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)

    # Sanitize filename
    safe_name = file.filename.replace("/", "_").replace("\\", "_")
    dest = upload_dir / safe_name
    # Handle name conflicts
    counter = 1
    while dest.exists():
        stem = Path(safe_name).stem
        suffix = Path(safe_name).suffix
        dest = upload_dir / f"{stem}_{counter}{suffix}"
        counter += 1

    dest.write_bytes(content)

    # Determine file type
    content_type = file.content_type or ""
    is_image = content_type.startswith("image/")

    result = {
        "filename": dest.name,
        "path": str(dest),
        "size": len(content),
        "content_type": content_type,
        "is_image": is_image,
    }

    # For images, also return base64 for vision API
    if is_image:
        b64 = base64.b64encode(content).decode("utf-8")
        result["base64"] = f"data:{content_type};base64,{b64}"

    return result


# ===================================================================
#  API: Settings
# ===================================================================

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
    return {"message": "Settings updated"}


# ===================================================================
#  API: System Info
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

def run_server(port: int = 18888, host: str = "0.0.0.0"):
    import uvicorn

    setup_static()

    from cbhcli_pkg import __version__
    print(f"CBHCLI Web v{__version__}")
    print(f"Server started: http://localhost:{port}")
    print(f"Config dir: {CBHCLI_DIR}")
    print("Press Ctrl+C to stop\n")

    import webbrowser
    webbrowser.open(f"http://localhost:{port}")

    uvicorn.run(app, host=host, port=port, log_level="warning")
