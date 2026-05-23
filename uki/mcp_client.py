"""
MCP 客户端

基于 JSON-RPC 2.0 的 MCP 协议实现。
通过 stdio 连接外部 MCP 服务器，动态发现和调用工具。
"""

import json
import subprocess
import threading
from pathlib import Path
from typing import Any

MCP_CONFIG_FILE = ".uki_mcp.json"


class MCPServerConnection:
    """与单个 MCP 服务器的连接"""

    def __init__(self, name: str, command: str, args: list[str]):
        self.name = name
        self.tools: list[dict] = []
        self._process: subprocess.Popen | None = None
        self._lock = threading.Lock()
        self._req_id = 0

        # 启动服务器进程（-u 强制无缓冲输出，DEVNULL 避免 stderr 管道阻塞）
        cmd_args = [command] + args
        if command in ("python", "python3"):
            cmd_args = [command, "-u"] + args

        self._process = subprocess.Popen(
            cmd_args,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        self._initialize()

    def _send(self, method: str, params: dict | None = None) -> dict:
        """发送 JSON-RPC 请求并等待响应"""
        with self._lock:
            self._req_id += 1
            request = {
                "jsonrpc": "2.0",
                "id": self._req_id,
                "method": method,
                "params": params or {},
            }
            req_str = json.dumps(request) + "\n"
            self._process.stdin.write(req_str)
            self._process.stdin.flush()

            # 读取响应（一行 JSON）
            resp_line = self._process.stdout.readline()
            if not resp_line:
                raise RuntimeError(f"MCP 服务器 {self.name} 无响应")
            response = json.loads(resp_line)
            if "error" in response:
                raise RuntimeError(f"MCP 错误: {response['error']}")
            return response.get("result", {})

    def _initialize(self):
        """发送 initialize 请求，获取工具列表"""
        result = self._send("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "Uki", "version": "1.0.0"},
        })
        # MCP 协议要求 initialize 后发送 initialized 通知
        self._notify("notifications/initialized", {})
        result = self._send("tools/list")
        raw_tools = result.get("tools", [])
        self.tools = [self._convert_tool(t) for t in raw_tools]

    def _notify(self, method: str, params: dict):
        """发送 JSON-RPC 通知（无 id，不等待响应）"""
        notification = {"jsonrpc": "2.0", "method": method, "params": params}
        self._process.stdin.write(json.dumps(notification) + "\n")
        self._process.stdin.flush()

    def _convert_tool(self, raw: dict) -> dict:
        """将 MCP 工具定义转为 OpenAI function calling 格式"""
        schema = raw.get("inputSchema", {})
        return {
            "type": "function",
            "function": {
                "name": raw["name"],
                "description": raw.get("description", ""),
                "parameters": schema,
            },
        }

    def call_tool(self, tool_name: str, arguments: dict) -> str:
        """调用工具并返回结果文本"""
        result = self._send("tools/call", {
            "name": tool_name,
            "arguments": arguments,
        })
        # MCP 工具结果在 content 数组中
        content = result.get("content", [])
        texts = []
        for item in content:
            if item.get("type") == "text":
                texts.append(item.get("text", ""))
        output = "\n".join(texts)
        if not output:
            output = "(工具返回了空结果)"
        if len(output) > 4000:
            output = output[:4000] + "\n...（输出过长，已截断）"
        return output

    def close(self):
        if self._process:
            self._process.terminate()
            self._process = None


class MCPManager:
    """管理所有 MCP 服务器连接"""

    def __init__(self):
        self.servers: list[MCPServerConnection] = []
        self._load_and_connect()

    def _load_and_connect(self):
        config_path = Path(MCP_CONFIG_FILE)
        if not config_path.exists():
            return

        try:
            data = json.loads(config_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, Exception):
            return

        for entry in data.get("servers", []):
            try:
                conn = MCPServerConnection(
                    name=entry["name"],
                    command=entry["command"],
                    args=entry.get("args", []),
                )
                self.servers.append(conn)
            except Exception as e:
                print(f"[MCP] 连接服务器 {entry.get('name', '?')} 失败: {e}")

    def get_definitions(self) -> list[dict]:
        """获取所有 MCP 工具定义"""
        result = []
        for srv in self.servers:
            result.extend(srv.tools)
        return result

    def execute(self, tool_name: str, arguments: dict) -> str | None:
        """在已连接的服务器中查找并执行工具。返回结果或 None。"""
        for srv in self.servers:
            for t in srv.tools:
                if t["function"]["name"] == tool_name:
                    return srv.call_tool(tool_name, arguments)
        return None
