"""Agent 链条核心模块

提供用户 Agent 之间的调用编排能力：
- AgentChain: 链条数据结构（层级 + 调用说明）
- ChainManager: CRUD + 持久化到 agent_chains.json
- ChainExecutor: 下游 Agent 调用执行器（加载完整配置 -> 独立会话 -> ReAct -> 回传）
"""
import json
import os
import threading
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional, Any, Callable

from cbhcli_pkg.core.agent import AgentManager, AgentConfig, AgentPersona


# ──────────────────────────────────────────────
#  数据结构
# ──────────────────────────────────────────────

@dataclass
class ChainAgent:
    """链条中一个 Agent 节点"""
    name: str
    call_instruction: str = ""

    def to_dict(self) -> dict:
        d = {"name": self.name}
        if self.call_instruction:
            d["call_instruction"] = self.call_instruction
        return d

    @classmethod
    def from_dict(cls, data) -> 'ChainAgent':
        if isinstance(data, str):
            return cls(name=data)
        return cls(
            name=data.get("name", ""),
            call_instruction=data.get("call_instruction", ""),
        )


@dataclass
class ChainLevel:
    """链条中的一个层级"""
    level: int
    agents: list[ChainAgent] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "level": self.level,
            "agents": [a.to_dict() for a in self.agents],
        }

    @classmethod
    def from_dict(cls, data) -> 'ChainLevel':
        return cls(
            level=data.get("level", 1),
            agents=[ChainAgent.from_dict(a) for a in data.get("agents", [])],
        )


@dataclass
class AgentChain:
    """完整的 Agent 链条"""
    name: str
    description: str = ""
    levels: list[ChainLevel] = field(default_factory=list)
    created_at: str = ""

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "levels": [l.to_dict() for l in self.levels],
            "created_at": self.created_at or datetime.now().isoformat(),
        }

    @classmethod
    def from_dict(cls, data) -> 'AgentChain':
        return cls(
            name=data.get("name", ""),
            description=data.get("description", ""),
            levels=[ChainLevel.from_dict(l) for l in data.get("levels", [])],
            created_at=data.get("created_at", ""),
        )

    # ── 查询方法 ──

    def get_root_agent(self) -> Optional[str]:
        """获取元 Agent (Level 1) 名称"""
        if self.levels and self.levels[0].agents:
            return self.levels[0].agents[0].name
        return None

    def get_level(self, level: int) -> Optional[ChainLevel]:
        for l in self.levels:
            if l.level == level:
                return l
        return None

    def get_agent_node(self, agent_name: str) -> Optional[ChainAgent]:
        """获取指定 Agent 在链条中的节点信息"""
        for level in self.levels:
            for agent in level.agents:
                if agent.name == agent_name:
                    return agent
        return None

    def get_agent_level(self, agent_name: str) -> Optional[int]:
        """获取指定 Agent 所在的层级"""
        for level in self.levels:
            for agent in level.agents:
                if agent.name == agent_name:
                    return level.level
        return None

    def get_downstream_agents(self, agent_name: str) -> list[str]:
        """获取指定 Agent 的直接下游 Agent 名称列表

        规则：agent_name 所在层级 +1 的所有 Agent 都是它的下游。
        （链条中同层所有 Agent 共享上游，因此下游 = 下一层全部 Agent）
        """
        current_level = self.get_agent_level(agent_name)
        if current_level is None:
            return []
        next_level = self.get_level(current_level + 1)
        if next_level is None:
            return []
        return [a.name for a in next_level.agents]

    def is_valid_downstream(self, upstream: str, downstream: str) -> bool:
        """检查 downstream 是否是 upstream 的合法下游"""
        return downstream in self.get_downstream_agents(upstream)

    def get_all_agent_names(self) -> list[str]:
        """获取链条中所有 Agent 名称"""
        names = []
        for level in self.levels:
            for agent in level.agents:
                if agent.name not in names:
                    names.append(agent.name)
        return names

    def validate(self, agent_manager: AgentManager) -> list[str]:
        """校验链条中引用的 Agent 是否都存在

        Returns:
            不存在的 Agent 名称列表
        """
        missing = []
        for name in self.get_all_agent_names():
            if not agent_manager.load_agent(name):
                missing.append(name)
        return missing


# ──────────────────────────────────────────────
#  ChainManager - CRUD + 持久化
# ──────────────────────────────────────────────

class ChainManager:
    """Agent 链条管理器"""

    def __init__(self, config_path: Optional[Path] = None):
        if config_path is None:
            config_path = Path.home() / ".cbhcli" / "agent_chains.json"
        self.config_path = config_path
        self._chains: dict[str, AgentChain] = {}
        self._lock = threading.Lock()
        self._load()
        self._mtime = self._file_mtime()

    def _file_mtime(self) -> float:
        try:
            return self.config_path.stat().st_mtime if self.config_path.exists() else 0.0
        except Exception:
            return 0.0

    def reload_if_changed(self) -> bool:
        """agent_chains.json 被其他进程修改时重载（跨进程链条同步，v5.2.2）。"""
        m = self._file_mtime()
        if m == self._mtime:
            return False
        self._mtime = m
        fresh: dict[str, AgentChain] = {}
        if self.config_path.exists():
            try:
                data = json.loads(self.config_path.read_text(encoding='utf-8'))
                for name, chain_data in data.get("chains", {}).items():
                    fresh[name] = AgentChain.from_dict(chain_data)
            except Exception:
                return False
        with self._lock:
            self._chains = fresh
        return True

    def _load(self):
        """从文件加载链条配置"""
        if not self.config_path.exists():
            return
        try:
            data = json.loads(self.config_path.read_text(encoding='utf-8'))
            for name, chain_data in data.get("chains", {}).items():
                self._chains[name] = AgentChain.from_dict(chain_data)
        except Exception:
            pass

    def _save(self):
        """保存链条配置到文件"""
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "chains": {name: chain.to_dict() for name, chain in self._chains.items()}
        }
        self.config_path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False),
            encoding='utf-8'
        )
        self._mtime = self._file_mtime()

    def list_chains(self) -> list[AgentChain]:
        """列出所有链条"""
        with self._lock:
            return list(self._chains.values())

    def get_chain(self, name: str) -> Optional[AgentChain]:
        """获取指定链条"""
        with self._lock:
            return self._chains.get(name)

    def add_chain(self, chain: AgentChain) -> bool:
        """添加链条"""
        with self._lock:
            if chain.name in self._chains:
                return False
            if not chain.created_at:
                chain.created_at = datetime.now().isoformat()
            self._chains[chain.name] = chain
            self._save()
            return True

    def update_chain(self, name: str, chain: AgentChain) -> bool:
        """更新链条"""
        with self._lock:
            if name not in self._chains:
                return False
            self._chains[chain.name] = chain
            if name != chain.name:
                del self._chains[name]
            self._save()
            return True

    def remove_chain(self, name: str) -> bool:
        """删除链条"""
        with self._lock:
            if name not in self._chains:
                return False
            del self._chains[name]
            self._save()
            return True

    def rename_chain(self, old_name: str, new_name: str) -> bool:
        """重命名链条"""
        with self._lock:
            if old_name not in self._chains or new_name in self._chains:
                return False
            chain = self._chains.pop(old_name)
            chain.name = new_name
            self._chains[new_name] = chain
            self._save()
            return True


# ──────────────────────────────────────────────
#  系统提示注入
# ──────────────────────────────────────────────

def build_chain_prompt(
    chain: AgentChain,
    agent_manager: AgentManager,
    current_agent_name: str,
) -> str:
    """构建注入到上游 Agent 系统提示的链条信息文本

    Args:
        chain: 当前激活的链条
        agent_manager: Agent管理器（用于读取下游 Agent 的 description）
        current_agent_name: 当前 Agent 名称

    Returns:
        链条信息文本，追加到系统提示末尾
    """
    current_level = chain.get_agent_level(current_agent_name)
    if current_level is None:
        return ""

    downstream = chain.get_downstream_agents(current_agent_name)
    if not downstream:
        # 当前 Agent 没有下游，不注入链条信息
        return ""

    lines = [
        "",
        "## Agent 链条信息",
        f"当前已激活链条: {chain.name}",
    ]
    if chain.description:
        lines.append(f"链条描述: {chain.description}")

    is_root = (current_level == 1)
    role = "元 Agent (Level 1)" if is_root else f"Level {current_level}"
    lines.append(f"你的角色: {role}")
    lines.append("")
    lines.append("你可以通过 call_agent 工具调用以下下游 Agent 执行任务:")
    lines.append("")

    next_level = chain.get_level(current_level + 1)
    if next_level:
        lines.append(f"Level {next_level.level}:")

        # 同时检查更下游的 Agent（用于展示完整链条）
        for agent_node in next_level.agents:
            name = agent_node.name
            # 实时从 AgentConfig 读取 description
            config = agent_manager.load_agent(name)
            desc = config.description if config else "(Agent 不存在)"

            lines.append(f"  - {name}: {desc}")
            if agent_node.call_instruction:
                lines.append(f"    调用说明: {agent_node.call_instruction}")

            # 展示该 Agent 的下游（如果有）
            sub_downstream = chain.get_downstream_agents(name)
            if sub_downstream:
                sub_level = chain.get_agent_level(name)
                sub_next = chain.get_level(sub_level + 1) if sub_level else None
                if sub_next:
                    lines.append(f"    {name} 的下游 (Level {sub_next.level}):")
                    for sub_agent in sub_next.agents:
                        sub_config = agent_manager.load_agent(sub_agent.name)
                        sub_desc = sub_config.description if sub_config else "(Agent 不存在)"
                        lines.append(f"      - {sub_agent.name}: {sub_desc}")
                        if sub_agent.call_instruction:
                            lines.append(f"        调用说明: {sub_agent.call_instruction}")

        lines.append("")

    lines.append(
        "调用方式: 使用 call_agent 工具，传入 agent_name 和 task 参数。"
        "同级多个下游 Agent 可在同一次回复中多次调用 call_agent 实现并行。"
        "下游 Agent 会以自己的完整身份（系统提示、工具、工作空间、记忆、技能）执行任务，"
        "完成后结果回传给你。"
    )

    return "\n".join(lines)


# ──────────────────────────────────────────────
#  ChainExecutor - 下游 Agent 调用执行器
# ──────────────────────────────────────────────

class ChainExecutor:
    """下游 Agent 调用执行器

    加载目标 Agent 的完整配置，构建独立会话，执行 ReAct 循环，
    将结果回传给上游 Agent。
    """

    def __init__(self, app):
        """
        Args:
            app: CBHCLIApp 实例（CLI 或 Web 兼容）
        """
        self._app = app

    def execute(
        self,
        chain: AgentChain,
        upstream_agent: str,
        target_agent: str,
        task: str,
        on_content: Optional[Callable] = None,
        on_tool: Optional[Callable] = None,
    ) -> str:
        """调用下游 Agent 执行任务

        Args:
            chain: 当前激活的链条
            upstream_agent: 上游 Agent 名称
            target_agent: 目标下游 Agent 名称
            task: 任务描述
            on_content: 流式内容回调 (agent_name, content) -> None
            on_tool: 工具调用回调 (agent_name, tool_name, args) -> None

        Returns:
            下游 Agent 的最终回复文本
        """
        from cbhcli_pkg.core.session import Session, Message
        from cbhcli_pkg.core.model import LLMClient
        from cbhcli_pkg.core.ai_handler import AIHandler
        from cbhcli_pkg.core.tool_executor import ToolExecutor
        from cbhcli_pkg.tools.registry import ToolRegistry
        from cbhcli_pkg.context.token_counter import get_token_counter
        from cbhcli_pkg.context.compressor import ContextCompressor
        from cbhcli_pkg.core.hooks import HookManager
        from cbhcli_pkg.core.checkpoint import CheckpointManager
        from cbhcli_pkg.core.tracer import Tracer
        from cbhcli_pkg.core.mcp_manager import MCPManager
        from cbhcli_pkg.core.skill_manager import SkillManager

        agent_manager = self._app.agent_manager

        # 1. 校验
        if not chain.is_valid_downstream(upstream_agent, target_agent):
            return f"错误: '{target_agent}' 不是 '{upstream_agent}' 的合法下游 Agent"

        # 2. 加载目标 Agent 完整配置
        config = agent_manager.load_agent(target_agent)
        if not config:
            return f"错误: Agent '{target_agent}' 不存在"

        persona = agent_manager.load_agent_persona(target_agent)

        # 3. 构建独立会话
        downstream_session = Session(agent_name=target_agent)

        # 4. 构建 LLM 客户端
        # 优先使用目标 Agent 的 primary_model，未配置则继承当前模型
        model_name = config.primary_model
        if model_name:
            model_config = self._app.global_config.get_model(model_name)
        else:
            # 继承当前会话的模型
            model_config = None
            if self._app.llm_client:
                # 复用当前 LLMClient
                llm_client = self._app.llm_client
            else:
                return "错误: 无可用模型"
        if model_name and model_config:
            llm_client = LLMClient(model_config)

        token_counter = get_token_counter(
            model_config.get("model") if model_config else
            (llm_client.model_name if hasattr(llm_client, 'model_name') else "")
        )

        # 5. 构建系统提示（含该 Agent 的 persona + 链条角色信息）
        memory_content = self._load_agent_memory(config.workspace_path)
        skill_manager = SkillManager(config.workspace_path)
        active_skills_prompt = skill_manager.build_skills_prompt()

        # 链条角色信息
        target_level = chain.get_agent_level(target_agent)
        chain_role = (
            f"\n\n## 链条角色信息\n"
            f"你当前在链条 '{chain.name}' 中，位于 Level {target_level}。\n"
            f"上游 Agent: {upstream_agent}\n"
            f"你的任务由上游 Agent 分派，请专注完成并返回结果。\n"
        )

        # 下游 Agent 的下游信息
        downstream_of_target = chain.get_downstream_agents(target_agent)
        if downstream_of_target:
            chain_role += f"你可以调用以下下游 Agent: {', '.join(downstream_of_target)}\n"
            chain_role += "使用 call_agent 工具调用下游 Agent。\n"

        supports_vision = llm_client.supports_vision if hasattr(llm_client, 'supports_vision') else False

        system_prompt = persona.build_system_prompt(
            agent_name=target_agent,
            model_name=llm_client.model_name if hasattr(llm_client, 'model_name') else "",
            memory_content=memory_content,
            active_skills_prompt=active_skills_prompt,
            cwd=os.getcwd(),
            supports_vision=supports_vision,
        )

        # 注入权限模式说明
        if getattr(self._app, 'permission_engine', None):
            from cbhcli_pkg.core.permissions import build_mode_note
            system_prompt += build_mode_note(self._app.permission_engine.mode)

        system_prompt += chain_role

        system_token_count = token_counter.count_tokens(system_prompt)
        downstream_session.add_message("system", system_prompt, token_count=system_token_count)

        # 6. 构建工具注册表（按目标 Agent 的 disabled_tools 过滤）
        downstream_registry = self._build_downstream_registry(
            config, target_agent, chain, downstream_of_target
        )

        # 7. 构建 ToolExecutor（挂载目标 Agent 的 Harness 组件）
        downstream_executor = ToolExecutor(downstream_registry)
        downstream_executor.permission_engine = getattr(self._app, 'permission_engine', None)  # 权限共享
        downstream_executor.hook_manager = HookManager(config.workspace_path, target_agent)
        downstream_executor.checkpoint_manager = CheckpointManager(config.workspace_path)
        # Tracer 写入上游 Agent（元 Agent）的 trace 文件
        if getattr(self._app, 'tracer', None):
            downstream_executor.tracer = self._app.tracer
        downstream_executor.session_id = downstream_session.id

        # 权限继承：与上游 Agent 保持一致的确认行为
        _confirm_cb = getattr(self._app, '_chain_confirm_callback', None)
        if _confirm_cb is not None:
            # Web 端：确认回调路由到 Web UI（SSE），回调内部处理 all 逻辑
            downstream_executor._confirm_callback = _confirm_cb
            downstream_executor.no_more_confirmations = False  # 回调优先
        else:
            # CLI 端：继承上游的 no_more_confirmations（上游已选 all 时下游同样免确认）
            _upstream_executor = getattr(self._app, 'tool_executor', None)
            if _upstream_executor is not None:
                downstream_executor.no_more_confirmations = getattr(
                    _upstream_executor, 'no_more_confirmations', False)

        # Web 端：ask_user 回调（下游 Agent 的 ask_user 路由到 Web UI）
        _ask_user_cb = getattr(self._app, '_chain_ask_user_callback', None)
        if _ask_user_cb is not None:
            downstream_executor._ask_user_callback = _ask_user_cb

        # 8. 构建上下文压缩器
        context_compressor = ContextCompressor(llm_client, token_counter)

        # 计算 tools schema tokens
        openai_tools = downstream_registry.get_openai_tools()
        tools_schema_tokens = 0
        if openai_tools:
            tools_schema_tokens = token_counter.count_tokens(
                json.dumps(openai_tools, ensure_ascii=False)
            )

        from cbhcli_pkg.core.session import ContextWindow
        from cbhcli_pkg.core.constants import DEFAULT_CONTEXT_LIMIT, DEFAULT_COMPRESSION_RATIO

        model_limit = llm_client.context_limit if hasattr(llm_client, 'context_limit') else DEFAULT_CONTEXT_LIMIT
        context_window = ContextWindow(
            model_limit=model_limit,
            compression_ratio=config.context_limit_ratio or DEFAULT_COMPRESSION_RATIO,
            tools_schema_tokens=tools_schema_tokens,
        )

        # 9. 创建 AIHandler 并执行
        handler = AIHandler(
            llm_client=llm_client,
            session=downstream_session,
            tool_executor=downstream_executor,
            token_counter=token_counter,
            is_subagent=True,  # 使用子 Agent 颜色样式
            display_label=f"[{target_agent}] ",
        )
        handler.agent_name = target_agent
        handler.context_compressor = context_compressor
        handler.context_window = context_window
        handler.auto_compress = config.auto_compress
        # 备用模型列表：下游 Agent 模型调用失败时自动 fallback（与主会话一致）
        try:
            from cbhcli_pkg.config.global_config import GlobalConfig
            handler.fallback_models = GlobalConfig().get_fallback_models()
        except Exception:
            handler.fallback_models = []

        # 设置流式回调
        if on_content or on_tool:
            handler._chain_content_callback = on_content
            handler._chain_tool_callback = on_tool

        # Web 端链条事件回调（SSE 推送下游 Agent 执行过程）
        _chain_evt_cb = getattr(self._app, '_chain_event_callback', None)
        if _chain_evt_cb is not None:
            handler._chain_event_callback = _chain_evt_cb

        # 执行
        result = handler.process_request(task)
        return result

    def _build_downstream_registry(
        self,
        config: AgentConfig,
        agent_name: str,
        chain: AgentChain,
        downstream_of_target: list[str],
    ) -> 'ToolRegistry':
        """为下游 Agent 构建独立的工具注册表

        按该 Agent 的 disabled_tools 过滤，同时注册 call_agent 工具（如果有下游）。
        """
        from cbhcli_pkg.tools.registry import ToolRegistry
        from cbhcli_pkg.tools.terminal import TerminalTool
        from cbhcli_pkg.tools.file_read import ReadTool
        from cbhcli_pkg.tools.file_write import WriteTool
        from cbhcli_pkg.tools.file_edit import EditTool
        from cbhcli_pkg.tools.python_tool import PythonTool
        from cbhcli_pkg.tools.skills_create import SkillsCreateTool
        from cbhcli_pkg.tools.delegate_task import DelegateTaskTool
        from cbhcli_pkg.tools.grep import GrepTool
        from cbhcli_pkg.tools.glob_tool import GlobTool
        from cbhcli_pkg.tools.ask_user import AskUserQuestionTool
        from cbhcli_pkg.tools.image import ImageTool
        from cbhcli_pkg.tools.process import ProcessTool
        from cbhcli_pkg.tools.kill_process import KillProcessTool
        from cbhcli_pkg.tools.todo import TodoTool

        registry = ToolRegistry()
        registry.register(TerminalTool())
        registry.register(ReadTool())
        registry.register(WriteTool())
        registry.register(EditTool())
        registry.register(PythonTool(f"chain_{agent_name}"))
        registry.register(DelegateTaskTool(self._app))
        registry.register(GrepTool())
        registry.register(GlobTool())
        registry.register(AskUserQuestionTool())
        registry.register(ImageTool(self._app))
        registry.register(ProcessTool())
        registry.register(KillProcessTool())
        registry.register(TodoTool())

        # 应用 Agent 工具开关
        registry.set_disabled_tools(config.disabled_tools or [])

        # 如果该 Agent 有下游，注册 call_agent 工具
        if downstream_of_target:
            from cbhcli_pkg.tools.call_agent import CallAgentTool
            registry.register(CallAgentTool(self._app, chain, agent_name))

        return registry

    @staticmethod
    def _load_agent_memory(workspace_path: Path) -> str:
        """读取 Agent 的 memory.md 内容（跳过模板说明部分）"""
        memory_file = workspace_path / "memory.md"
        if not memory_file.exists():
            return ""
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
            return ""


# ──────────────────────────────────────────────
#  树形展示工具
# ──────────────────────────────────────────────

def render_chain_tree(
    chain: AgentChain,
    agent_manager: AgentManager,
    show_details: bool = False,
) -> str:
    """渲染链条树形结构

    Args:
        chain: 链条
        agent_manager: Agent管理器
        show_details: 是否显示详细信息（模型名、描述等）
    """
    lines = []

    for i, level in enumerate(chain.levels):
        prefix_parent = "  " * (i - 1) if i > 0 else ""
        for j, agent_node in enumerate(level.agents):
            if i == 0:
                # 根 Agent
                lines.append(f"{agent_node.name}")
            else:
                prefix = prefix_parent
                is_last = (j == len(level.agents) - 1)
                connector = "└── " if is_last else "├── "
                instr = f"  [{agent_node.call_instruction}]" if agent_node.call_instruction else ""
                lines.append(f"{prefix}{connector}{agent_node.name}{instr}")

            if show_details:
                config = agent_manager.load_agent(agent_node.name)
                if config:
                    indent = "  " * (i + 1)
                    if config.description:
                        lines.append(f"{indent}描述: {config.description}")
                    model = config.primary_model or "(继承)"
                    lines.append(f"{indent}模型: {model}")
                    if agent_node.call_instruction and i > 0:
                        lines.append(f"{indent}调用说明: {agent_node.call_instruction}")

    return "\n".join(lines)
