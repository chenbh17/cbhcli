"""MCP 客户端 - Streamable HTTP 协议"""
import json
import re
import requests
from typing import Optional


class MCPClient:
    """MCP Streamable HTTP 客户端
    
    通过 HTTP POST 请求与 MCP 服务器通信。
    支持 MCP JSON-RPC 协议，处理 SSE (text/event-stream) 响应。
    """
    
    def __init__(self, url: str, headers: Optional[dict] = None, timeout: int = 30):
        """
        Args:
            url: MCP 服务器的 Streamable HTTP URL
            headers: 额外的 HTTP 头（如认证）
            timeout: 请求超时时间（秒）
        """
        self.url = url.rstrip('/')
        self.headers = headers or {}
        self.timeout = timeout
        self._request_id = 0
        self._session_id: Optional[str] = None  # MCP Session ID
        
        # MCP Streamable HTTP 协议要求的 Accept 头
        # 必须同时接受 application/json 和 text/event-stream
        self.headers.setdefault('Accept', 'application/json, text/event-stream')
        self.headers.setdefault('Content-Type', 'application/json')
    
    def _next_id(self) -> int:
        """生成下一个请求 ID"""
        self._request_id += 1
        return self._request_id
    
    def _parse_sse_response(self, response: requests.Response) -> dict:
        """解析 SSE 响应或普通 JSON 响应
        
        Args:
            response: HTTP 响应对象
            
        Returns:
            JSON-RPC 响应结果
        """
        content_type = response.headers.get('Content-Type', '')
        
        # 如果是 SSE 流
        if 'text/event-stream' in content_type:
            # 确保使用 UTF-8 解码，避免中文乱码
            text = response.text
            # 如果 requests 没有使用正确的编码，强制用 UTF-8 重新解码
            if response.encoding and response.encoding.lower() in ('iso-8859-1', 'latin-1'):
                text = response.content.decode('utf-8')
            return self._parse_sse_stream(text)
        
        # 否则当作普通 JSON
        # 确保使用 UTF-8 解码
        if response.encoding and response.encoding.lower() in ('iso-8859-1', 'latin-1'):
            response.encoding = 'utf-8'
        return response.json()
    
    def _parse_sse_stream(self, text: str) -> dict:
        """解析 SSE 格式的文本，提取 JSON-RPC 响应
        
        SSE 格式:
        event: message
        data: {"jsonrpc": "2.0", "id": 1, "result": {...}}
        
        或者可能是多行 data
        
        Args:
            text: SSE 格式的文本
            
        Returns:
            解析后的 JSON 对象
        """
        # 尝试多种方式提取 JSON
        
        # 方法1: 提取 data: 后面的内容
        data_pattern = r'data:\s*({.*})'
        matches = re.findall(data_pattern, text, re.DOTALL)
        
        if matches:
            # 合并多行 data（如果存在）
            full_data = ''.join(matches)
            try:
                return json.loads(full_data)
            except json.JSONDecodeError:
                pass
        
        # 方法2: 逐行解析 SSE
        lines = text.split('\n')
        data_lines = []
        in_data = False
        
        for line in lines:
            line = line.strip()
            if line.startswith('data:'):
                in_data = True
                data_content = line[5:].strip()
                if data_content:
                    data_lines.append(data_content)
            elif line == '' and in_data:
                # 空行表示一个事件结束
                if data_lines:
                    full_data = '\n'.join(data_lines)
                    try:
                        return json.loads(full_data)
                    except json.JSONDecodeError:
                        pass
                    data_lines = []
                    in_data = False
            elif not line.startswith(('event:', 'id:', 'retry:', ':')):
                # 非 SSE 行，重置
                if data_lines:
                    data_lines = []
                in_data = False
        
        # 方法3: 尝试从文本中直接找到 JSON 对象
        json_match = re.search(r'(\{.*"jsonrpc"\s*:\s*"2\.0".*\})', text, re.DOTALL)
        if json_match:
            try:
                return json.loads(json_match.group(1))
            except json.JSONDecodeError:
                pass
        
        # 方法4: 尝试找到任何 JSON 对象
        json_match = re.search(r'(\{.*\})', text, re.DOTALL)
        if json_match:
            try:
                return json.loads(json_match.group(1))
            except json.JSONDecodeError:
                pass
        
        raise Exception(f"无法解析 SSE 响应: {text[:500]}")
    
    def _send_request(self, method: str, params: Optional[dict] = None) -> dict:
        """发送 JSON-RPC 请求
        
        Args:
            method: JSON-RPC 方法名
            params: 方法参数
            
        Returns:
            JSON-RPC 响应结果
            
        Raises:
            Exception: 请求失败或 JSON-RPC 错误
        """
        payload = {
            "jsonrpc": "2.0",
            "id": self._next_id(),
            "method": method,
            "params": params or {}
        }
        
        # 构建请求头（包含 session ID）
        request_headers = dict(self.headers)
        if self._session_id:
            request_headers['Mcp-Session-Id'] = self._session_id
        
        response = requests.post(
            self.url,
            json=payload,
            headers=request_headers,
            timeout=self.timeout
        )
        
        if response.status_code not in (200, 201, 202):
            raise Exception(f"MCP 请求失败: HTTP {response.status_code} - {response.text[:500]}")
        
        # 保存服务器返回的 Session ID (处理大小写)
        new_session_id = response.headers.get('Mcp-Session-Id') or response.headers.get('mcp-session-id')
        if new_session_id:
            self._session_id = new_session_id
        
        try:
            result = self._parse_sse_response(response)
        except json.JSONDecodeError:
            raise Exception(f"MCP 响应解析失败: {response.text[:500]}")
        
        # 检查 JSON-RPC 错误
        if "error" in result:
            error = result["error"]
            raise Exception(f"MCP 错误 [{error.get('code', '?')}]: {error.get('message', '未知错误')}")
        
        return result.get("result", {})
    
    def initialize(self) -> dict:
        """初始化 MCP 连接
        
        Returns:
            服务器能力信息
        """
        return self._send_request("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {
                "name": "CBHCLI",
                "version": "4.7.9"
            }
        })
    
    def list_tools(self) -> list[dict]:
        """列出 MCP 服务器上的所有工具
        
        Returns:
            工具列表，每个工具包含 name, description, inputSchema
        """
        result = self._send_request("tools/list")
        return result.get("tools", [])
    
    def call_tool(self, tool_name: str, arguments: dict) -> list[dict]:
        """调用 MCP 工具
        
        Args:
            tool_name: 工具名称
            arguments: 工具参数
            
        Returns:
            工具执行结果（MCP Content 数组）
        """
        result = self._send_request("tools/call", {
            "name": tool_name,
            "arguments": arguments
        })
        # MCP tools/call 返回 {content: [...], isError: bool}
        content = result.get("content", [])
        
        # 确保返回的是列表
        if isinstance(content, str):
            # 某些服务器可能返回字符串而不是数组
            content = [{"type": "text", "text": content}]
        
        return content
    
    def ping(self) -> bool:
        """测试 MCP 连接
        
        Returns:
            是否连接成功
        """
        try:
            self._send_request("ping")
            return True
        except Exception:
            return False
