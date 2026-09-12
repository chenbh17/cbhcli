"""MCP 管理器 - 每个 Agent 独立管理 MCP 连接"""
import hashlib
import json
import os
import re
import threading
import time
from pathlib import Path
from typing import Optional

from cbhcli_pkg.core.mcp_client import MCPClient
from cbhcli_pkg.core.mcp_tool_adapter import MCPToolAdapter
from cbhcli_pkg.tools.registry import ToolRegistry


# ${VAR} / $VAR 环境变量占位符（v5.3.2）
_ENV_VAR_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}|\$([A-Za-z_][A-Za-z0-9_]*)")

# ===================================================================
#  进程级 MCP 连接缓存（v5.3.3）
# ===================================================================
# 问题：每次创建会话（Web 新建会话/恢复历史会话/切 Agent）都会重新
# initialize 所有 MCP 服务器，实测每次 0.8~1.0 秒（纯网络握手），
# 是"加载卡顿"的最大来源。
# 方案：同一进程内按「mcp.json 内容」缓存已建立的连接与工具定义，
# 后续会话直接从缓存重建工具（毫秒级，零网络请求）。
# 失效条件：mcp.json 内容变化（增删服务器/改 headers/启停工具）→
# 缓存 key 变化 → 自动重新连接；手动 refresh 也会刷新缓存。
_CONN_CACHE: dict = {}                 # key -> {server_name: entry}
_CONN_CACHE_LOCK = threading.Lock()
_CONN_CACHE_MAX = 16                   # 最多保留多少份不同配置的缓存
_CONN_RETRY_AFTER = 120.0              # 连接失败后多久允许重试（秒）

# entry: {"client": MCPClient|None, "tools": list[dict], "url": str,
#         "failed_at": float}



def _expand_env_vars(value: str) -> tuple[str, list[str]]:
    """展开 ${VAR} / $VAR 占位符。

    未设置的环境变量保留占位符原样，并计入缺失列表（由调用方提示用户）。

    Returns:
        (展开后的字符串, 缺失的环境变量名列表)
    """
    missing: list[str] = []

    def _replace(match):
        var = match.group(1) or match.group(2)
        actual = os.environ.get(var)
        if actual is None:
            missing.append(var)
            return match.group(0)
        return actual

    return _ENV_VAR_PATTERN.sub(_replace, value), missing


def _conn_cache_key(config_file, servers: list[dict]) -> str:
    """按「配置文件 + 服务器配置内容」生成缓存 key（v5.3.3）

    用内容而非 mtime：连接过程本身会重写 mcp.json（_save_config），
    mtime 每次都会变，用它做 key 会导致缓存永不命中。
    """
    try:
        payload = json.dumps(servers, sort_keys=True, ensure_ascii=False)
    except Exception:
        payload = str(servers)
    digest = hashlib.md5(payload.encode("utf-8")).hexdigest()[:16]
    return f"{config_file}|{digest}"


def _conn_cache_get(key: str) -> Optional[dict]:
    with _CONN_CACHE_LOCK:
        entry = _CONN_CACHE.get(key)
        return dict(entry) if entry else None


def _conn_cache_update_server(key: str, server_name: str, entry: dict) -> None:
    with _CONN_CACHE_LOCK:
        bucket = _CONN_CACHE.setdefault(key, {})
        bucket[server_name] = dict(entry)
        while len(_CONN_CACHE) > _CONN_CACHE_MAX:
            _CONN_CACHE.pop(next(iter(_CONN_CACHE)))



class MCPManager:
    """MCP 管理器
    
    每个 Agent 独立管理自己的 MCP 连接和工具。
    配置存储在 ~/.cbhcli/agents/<agent>/mcp.json 中。
    """
    
    def __init__(self, agent_name: str, agent_workspace: Path, tool_registry: ToolRegistry):
        """
        Args:
            agent_name: Agent 名称
            agent_workspace: Agent 工作空间路径
            tool_registry: 工具注册中心
        """
        self.agent_name = agent_name
        self.agent_workspace = agent_workspace
        self.tool_registry = tool_registry
        self.config_file = agent_workspace / "mcp.json"
        
        # MCP 服务器配置列表
        # 格式: [{"name": "xxx", "url": "http://...", "headers": {...}, "enabled_tools": ["tool1", "tool2"]}]
        self._servers: list[dict] = []
        
        # 活跃的 MCP 客户端和工具
        self._clients: dict[str, MCPClient] = {}  # name -> client
        self._mcp_tools: dict[str, MCPToolAdapter] = {}  # mcp_tool_name -> adapter

        # 加载配置
        self._load_config()
        self._mtime = self._file_mtime()

    def _file_mtime(self) -> float:
        try:
            return self.config_file.stat().st_mtime if self.config_file.exists() else 0.0
        except Exception:
            return 0.0

    def reload_if_changed(self) -> bool:
        """mcp.json 被其他进程修改时重载（跨进程 MCP 配置同步，v5.2.2）。

        重新读取服务器列表：移除已删除服务器的客户端/工具，连接新增服务器。
        连接为尽力而为（失败不抛错，list_servers 会标记未连接）。
        """
        m = self._file_mtime()
        if m == self._mtime:
            return False
        self._mtime = m
        fresh_servers: list[dict] = []
        if self.config_file.exists():
            try:
                with open(self.config_file, 'r', encoding='utf-8') as f:
                    fresh_servers = json.load(f).get("servers", [])
            except Exception:
                return False
        old_names = {s["name"] for s in self._servers}
        self._servers = fresh_servers
        new_names = {s["name"] for s in self._servers}
        # 断开并注销已被删除的服务器
        for name in list(self._clients.keys()):
            if name not in new_names:
                try:
                    self._unregister_server_tools(name)
                except Exception:
                    pass
                self._clients.pop(name, None)
        # 连接新增的服务器（尽力而为）
        for s in self._servers:
            if s["name"] not in old_names or s["name"] not in self._clients:
                try:
                    self._connect_server(s)
                except Exception:
                    pass
        return True

    def _load_config(self):
        """加载 MCP 配置并自动连接所有服务器"""
        if self.config_file.exists():
            try:
                with open(self.config_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                self._servers = data.get("servers", [])
            except (json.JSONDecodeError, KeyError):
                self._servers = []
        
        # 自动连接所有已配置的服务器
        self._connect_all_servers()
    
    def _connect_all_servers(self):
        """连接所有已配置的服务器（v5.3.3：优先复用进程级连接缓存）

        缓存命中时直接克隆已握手的客户端 + 复用工具定义注册（毫秒级，
        零网络请求）；未命中才真正连接并写回缓存。这样同一进程内
        新建会话/恢复会话/切 Agent 都不再重复 0.8~1.0 秒的 MCP 握手。
        """
        key = _conn_cache_key(self.config_file, self._servers)
        cached = _conn_cache_get(key)
        if cached is not None:
            # ---- 缓存命中：逐服务器重建工具（无网络请求）----
            now = time.time()
            missing = cached.get("_missing") or {}
            for server in self._servers:
                name = server.get("name")
                entry = cached.get(name)
                if entry is None:
                    # 缓存里没有（上次连接失败）：短时间内部不重试，
                    # 避免每次创建会话都卡在失败服务器的超时上
                    if now - float(missing.get(name, 0.0)) < _CONN_RETRY_AFTER:
                        continue
                    if not self._try_connect(server):
                        missing[name] = now
                        _conn_cache_update_server(key, "_missing", missing)
                    continue
                if entry.get("client") is None:
                    # 负缓存（上次连接失败）
                    if now - float(entry.get("failed_at", 0.0)) < _CONN_RETRY_AFTER:
                        continue
                    self._try_connect(server)
                    continue
                try:
                    self._register_cached_server(server, entry)
                except Exception:
                    pass
            return

        # ---- 缓存未命中：真正连接（首次 / 配置变化）----
        for server in self._servers:
            try:
                self._try_connect(server)
            except Exception:
                # 连接失败不阻塞启动
                pass

    def _try_connect(self, server: dict) -> bool:
        """连接一台服务器并写缓存（失败写负缓存，v5.3.3）

        Returns:
            是否连接成功
        """
        name = server.get("name") or ""
        ok = False
        try:
            self._connect_server(server)      # 成功后内部写缓存
            ok = name in self._clients
        except Exception:
            ok = False
        if not ok:
            key = _conn_cache_key(self.config_file, self._servers)
            _conn_cache_update_server(key, name, {
                "client": None, "tools": [], "failed_at": time.time(),
                "url": server.get("url", ""),
            })
        return ok

    def _register_cached_server(self, server: dict, entry: dict) -> int:
        """用缓存条目重建某服务器的工具（不发起网络请求，v5.3.3）

        Returns:
            注册的工具数量
        """
        name = server.get("name") or ""
        base_client = entry.get("client")
        if base_client is None:
            return 0
        client = base_client.clone()      # 独立请求计数，共享 MCP 会话
        tools = entry.get("tools") or []
        enabled_tools = server.get("enabled_tools")  # None = 全部启用
        if enabled_tools is not None:
            tools = [t for t in tools if t.get("name") in enabled_tools]
        registered = 0
        for tool_info in tools:
            try:
                adapter = MCPToolAdapter(client, tool_info, name)
                self._mcp_tools[adapter.name] = adapter
                self.tool_registry.register(adapter)
                registered += 1
            except Exception:
                continue
        self._clients[name] = client
        return registered

    
    def _save_config(self):
        """保存 MCP 配置"""
        data = {"servers": self._servers}
        with open(self.config_file, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        self._mtime = self._file_mtime()
    
    def add_server(self, name: str, url: str, headers: Optional[dict] = None,
                   enabled_tools: Optional[list[str]] = None) -> str:
        """添加 MCP 服务器
        
        Args:
            name: 服务器名称（Agent 内唯一）
            url: MCP 服务器 URL
            headers: 额外的 HTTP 头
            enabled_tools: 启用的工具列表（None 表示全部启用）
            
        Returns:
            状态消息
        """
        # 检查名称是否已存在
        if any(s["name"] == name for s in self._servers):
            return f"❌ MCP 服务器 '{name}' 已存在，请先删除后再添加"
        
        server_config = {
            "name": name,
            "url": url,
            "headers": headers or {},
            "enabled_tools": enabled_tools  # None 表示全部启用
        }
        
        self._servers.append(server_config)
        self._save_config()
        
        # 连接并注册工具
        return self._connect_server(server_config)
    
    def remove_server(self, name: str) -> str:
        """移除 MCP 服务器
        
        Args:
            name: 服务器名称
            
        Returns:
            状态消息
        """
        for i, server in enumerate(self._servers):
            if server["name"] == name:
                # 注销该服务器的所有工具
                self._unregister_server_tools(name)
                
                # 移除客户端
                if name in self._clients:
                    del self._clients[name]
                
                # 移除配置
                self._servers.pop(i)
                self._save_config()
                return f"✅ 已移除 MCP 服务器: {name}"
        
        return f"❌ 找不到 MCP 服务器: {name}"
    
    def list_servers(self) -> list[dict]:
        """列出所有 MCP 服务器
        
        Returns:
            服务器信息列表
        """
        result = []
        for server in self._servers:
            name = server["name"]
            is_connected = name in self._clients
            
            # 获取该服务器的工具列表
            server_tools = []
            for tool_name, adapter in self._mcp_tools.items():
                if adapter._server_name == name:
                    server_tools.append({
                        "name": tool_name,
                        "mcp_name": adapter._tool_info["name"],
                        "description": adapter._tool_info.get("description", ""),
                        "enabled": True  # 所有已注册的都是启用的
                    })
            
            result.append({
                "name": name,
                "url": server["url"],
                "connected": is_connected,
                "tools": server_tools,
                "enabled_tools": server.get("enabled_tools")
            })
        
        return result
    
    def get_server_tools(self, name: str) -> list[dict]:
        """获取指定服务器的已启用工具列表（仅已注册的）
        
        Args:
            name: 服务器名称
            
        Returns:
            工具信息列表
        """
        tools = []
        for tool_name, adapter in self._mcp_tools.items():
            if adapter._server_name == name:
                tools.append({
                    "name": tool_name,
                    "mcp_name": adapter._tool_info["name"],
                    "description": adapter._tool_info.get("description", "")
                })
        return tools

    def get_all_server_tools(self, name: str) -> list[dict]:
        """获取指定服务器的全部工具列表（包括已禁用的），从客户端实时查询
        
        Args:
            name: 服务器名称
            
        Returns:
            工具信息列表，每项含 name, description, enabled 字段
        """
        # 查找服务器配置
        server = None
        for s in self._servers:
            if s["name"] == name:
                server = s
                break
        if not server:
            return []

        # 从客户端获取全部工具
        client = self._clients.get(name)
        if not client:
            return []

        try:
            all_tools = client.list_tools()
        except Exception:
            return []

        enabled_tools = server.get("enabled_tools")  # None = 全部启用

        # 获取当前已注册的工具名（用于判断启用状态）
        registered_mcp_names = set()
        for adapter in self._mcp_tools.values():
            if adapter._server_name == name:
                registered_mcp_names.add(adapter._tool_info["name"])

        result = []
        for t in all_tools:
            tool_name = t["name"]
            if enabled_tools is None:
                is_enabled = True
            else:
                is_enabled = tool_name in enabled_tools
            result.append({
                "name": tool_name,
                "description": t.get("description", ""),
                "enabled": is_enabled,
            })
        return result
    
    def toggle_tool(self, server_name: str, tool_name: str, enable: bool) -> str:
        """启用/禁用 MCP 工具
        
        Args:
            server_name: 服务器名称
            tool_name: MCP 工具名称（原始名称，不含 mcp_ 前缀）
            enable: True=启用，False=禁用
            
        Returns:
            状态消息
        """
        # 找到服务器配置
        server = None
        for s in self._servers:
            if s["name"] == server_name:
                server = s
                break
        
        if not server:
            return f"❌ 找不到 MCP 服务器: {server_name}"
        
        enabled_tools = server.get("enabled_tools")  # None 表示全部启用
        
        if enable:
            # 启用工具
            if enabled_tools is None:
                # 全部已启用，无需操作
                return f"ℹ️  工具 '{tool_name}' 已经是启用状态"
            
            if tool_name not in enabled_tools:
                enabled_tools.append(tool_name)
            
            # 注册工具
            return self._register_tool_from_server(server)
        else:
            # 禁用工具
            if enabled_tools is None:
                # 需要改为显式列表（排除该工具）
                # 先获取所有工具名称
                server_config = self._servers[0]  # 需要重新获取
                for s in self._servers:
                    if s["name"] == server_name:
                        server_config = s
                        break
                
                # 获取所有可用工具
                try:
                    client = self._clients.get(server_name)
                    if not client:
                        headers, _ = self._client_headers(server_config)
                        client = MCPClient(server_config["url"], headers)
                        client.initialize()
                    
                    all_tools = client.list_tools()
                    all_tool_names = [t["name"] for t in all_tools]
                    enabled_tools = [t for t in all_tool_names if t != tool_name]
                    server["enabled_tools"] = enabled_tools
                except Exception as e:
                    return f"❌ 获取工具列表失败: {str(e)}"
            else:
                if tool_name in enabled_tools:
                    enabled_tools.remove(tool_name)
            
            # 注销工具
            self._unregister_tool(server_name, tool_name)
            self._save_config()
            return f"✅ 已禁用工具: {server_name} -> {tool_name}"
    
    def refresh_server(self, name: str) -> str:
        """重新连接 MCP 服务器并刷新工具
        
        Args:
            name: 服务器名称
            
        Returns:
            状态消息
        """
        server = None
        for s in self._servers:
            if s["name"] == name:
                server = s
                break
        
        if not server:
            return f"❌ 找不到 MCP 服务器: {name}"
        
        # 注销旧工具
        self._unregister_server_tools(name)
        if name in self._clients:
            del self._clients[name]
        
        # 重新连接
        return self._connect_server(server)
    
    def _client_headers(self, server: dict) -> tuple[dict, list[str]]:
        """生成用于实际连接的 headers 副本（v5.3.2）。

        - 对值中的 ${VAR} / $VAR 占位符做环境变量展开：配置里可只存占位符，
          API Key 不必明文落盘到 mcp.json
        - 返回副本而非原地修改：展开后的明文不会写回配置文件
        - 同时返回缺失的环境变量名列表，供连接失败时提示用户

        Returns:
            (headers 副本, 缺失的环境变量名列表)
        """
        headers = dict(server.get("headers") or {})
        missing: list[str] = []
        for key, value in list(headers.items()):
            if isinstance(value, str) and "$" in value:
                headers[key], miss = _expand_env_vars(value)
                missing.extend(miss)
        return headers, missing

    def _connect_server(self, server: dict) -> str:
        """连接 MCP 服务器并注册工具
        
        Args:
            server: 服务器配置
            
        Returns:
            状态消息
        """
        name = server["name"]
        url = server["url"]
        headers, missing_env = self._client_headers(server)
        
        try:
            # 创建客户端
            client = MCPClient(url, headers)
            
            # 初始化
            client.initialize()
            
            # 获取工具列表
            tools = client.list_tools()
            all_tools = list(tools)      # 缓存用全量（注册时再按 enabled 过滤）
            
            if not tools:
                self._clients[name] = client
                self._save_config()
                self._cache_conn_result(server, client, all_tools)
                return f"⚠️  已连接 MCP 服务器: {name}（无可用工具）"
            
            # 过滤启用的工具
            enabled_tools = server.get("enabled_tools")  # None = 全部启用
            if enabled_tools is not None:
                tools = [t for t in tools if t["name"] in enabled_tools]
            
            # 注册工具
            registered = 0
            for tool_info in tools:
                adapter = MCPToolAdapter(client, tool_info, name)
                self._mcp_tools[adapter.name] = adapter
                self.tool_registry.register(adapter)
                registered += 1
            
            self._clients[name] = client
            self._save_config()
            self._cache_conn_result(server, client, all_tools)
            
            return f"✅ 已添加 MCP 服务器: {name}（{registered} 个工具）"
            
        except Exception as e:
            message = f"❌ 连接 MCP 服务器失败: {name}\n错误: {str(e)}"
            if missing_env:
                message += ("\n⚠️ 环境变量未设置: " + ", ".join(missing_env)
                            + "（headers 中的 ${...} 占位符无法展开，请先 export 后再试）")
            return message

    def _cache_conn_result(self, server: dict, client: MCPClient,
                           tools: list) -> None:
        """把连接结果写入进程级缓存（v5.3.3，供后续会话零网络重建工具）"""
        try:
            key = _conn_cache_key(self.config_file, self._servers)
            _conn_cache_update_server(key, server.get("name") or "", {
                "client": client, "tools": list(tools or []),
                "url": server.get("url", ""), "failed_at": 0.0,
            })
        except Exception:
            pass

    
    def _register_tool_from_server(self, server: dict) -> str:
        """从服务器重新注册所有启用的工具"""
        name = server["name"]
        
        # 注销旧工具
        self._unregister_server_tools(name)
        if name in self._clients:
            del self._clients[name]
        
        return self._connect_server(server)
    
    def _unregister_tool(self, server_name: str, tool_name: str):
        """注销单个 MCP 工具
        
        Args:
            server_name: 服务器名称
            tool_name: MCP 工具原始名称
        """
        # 尝试多种可能的注册名称格式
        candidates = [
            f"mcp_{tool_name}",
            f"mcp_{server_name}_{tool_name}",
            tool_name,
        ]
        for candidate in candidates:
            if candidate in self._mcp_tools:
                self.tool_registry.unregister(candidate)
                del self._mcp_tools[candidate]
                return
    
    def _unregister_server_tools(self, server_name: str):
        """注销指定服务器的所有工具"""
        to_remove = []
        for tool_name, adapter in self._mcp_tools.items():
            if adapter._server_name == server_name:
                to_remove.append(tool_name)
        
        for tool_name in to_remove:
            self.tool_registry.unregister(tool_name)
            del self._mcp_tools[tool_name]
    
    def get_tool_descriptions(self) -> str:
        """获取所有 MCP 工具的描述（用于系统提示）"""
        if not self._mcp_tools:
            return ""
        
        parts = []
        for adapter in self._mcp_tools.values():
            parts.append(f"- {adapter.name}: {adapter.description}")
        
        return "\n".join(parts)
    
    def close_all(self):
        """关闭所有 MCP 连接"""
        self._clients.clear()
        self._mcp_tools.clear()
