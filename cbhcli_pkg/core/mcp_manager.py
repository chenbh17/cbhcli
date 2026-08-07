"""MCP 管理器 - 每个 Agent 独立管理 MCP 连接"""
import json
from pathlib import Path
from typing import Optional

from cbhcli_pkg.core.mcp_client import MCPClient
from cbhcli_pkg.core.mcp_tool_adapter import MCPToolAdapter
from cbhcli_pkg.tools.registry import ToolRegistry


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
        """连接所有已配置的服务器"""
        for server in self._servers:
            try:
                self._connect_server(server)
            except Exception:
                # 连接失败不阻塞启动
                pass
    
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
                        client = MCPClient(server_config["url"], server_config.get("headers", {}))
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
    
    def _connect_server(self, server: dict) -> str:
        """连接 MCP 服务器并注册工具
        
        Args:
            server: 服务器配置
            
        Returns:
            状态消息
        """
        name = server["name"]
        url = server["url"]
        headers = server.get("headers", {})
        
        try:
            # 创建客户端
            client = MCPClient(url, headers)
            
            # 初始化
            client.initialize()
            
            # 获取工具列表
            tools = client.list_tools()
            
            if not tools:
                self._clients[name] = client
                self._save_config()
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
            
            return f"✅ 已添加 MCP 服务器: {name}（{registered} 个工具）"
            
        except Exception as e:
            return f"❌ 连接 MCP 服务器失败: {name}\n错误: {str(e)}"
    
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
