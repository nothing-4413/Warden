"""MCP（Model Context Protocol）最小客户端：stdio 传输 + JSON-RPC 2.0。

零第三方依赖：直接 subprocess 拉起 MCP server，按行收发 JSON-RPC。
规范参考：https://modelcontextprotocol.io/specification/2024-11-05/
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass


@dataclass
class MCPTool:
    name: str
    description: str
    input_schema: dict


class MCPError(Exception):
    """MCP 协议 / 进程 / 调用错误。"""


class MCPClient:
    def __init__(self, command: list[str]) -> None:
        self._proc = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            bufsize=1,
        )
        self._id = 0
        self.initialize()

    def initialize(self) -> None:
        self._request(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "warden", "version": "0.4.0"},
            },
        )
        # 初始化完成通知（notification：无 id，不期待响应）
        self._notify("notifications/initialized", {})

    def _request(self, method: str, params: dict) -> dict:
        self._id += 1
        msg = {"jsonrpc": "2.0", "id": self._id, "method": method, "params": params}
        self._proc.stdin.write(json.dumps(msg) + "\n")
        self._proc.stdin.flush()
        while True:
            line = self._proc.stdout.readline()
            if not line:
                raise MCPError(f"MCP server closed stdout while awaiting {method}")
            data = json.loads(line)
            # 跳过非本请求的响应/通知，直到等到匹配 id 的响应
            if data.get("id") == self._id:
                if "error" in data:
                    raise MCPError(f"{method} failed: {data['error']}")
                return data.get("result", {})

    def _notify(self, method: str, params: dict) -> None:
        msg = {"jsonrpc": "2.0", "method": method, "params": params}
        self._proc.stdin.write(json.dumps(msg) + "\n")
        self._proc.stdin.flush()

    def list_tools(self) -> list[MCPTool]:
        result = self._request("tools/list", {})
        return [
            MCPTool(
                name=t["name"],
                description=t.get("description", ""),
                input_schema=t.get("inputSchema", {}),
            )
            for t in result.get("tools", [])
        ]

    def call_tool(self, name: str, arguments: dict) -> str:
        result = self._request("tools/call", {"name": name, "arguments": arguments})
        text = _extract_text(result)
        if result.get("isError"):
            raise MCPError(f"tool {name} returned error: {text}")
        return text

    def close(self) -> None:
        # stdin + stdout 都要关：只关 stdin 会让 stdout 的 TextIOWrapper 留到解释器
        # 退出时才被回收，测试/长驻进程里表现为 ResourceWarning: unclosed file。
        for pipe in (self._proc.stdin, self._proc.stdout):
            if pipe is not None:
                try:
                    pipe.close()
                except OSError:
                    pass
        self._proc.terminate()
        try:
            self._proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self._proc.kill()


def _extract_text(result: dict) -> str:
    """MCP tools/call 返回 content: [{type:"text", text}]，取所有 text 拼接。"""
    parts = []
    for c in result.get("content", []):
        if c.get("type") == "text":
            parts.append(c.get("text", ""))
    return "\n".join(parts) if parts else json.dumps(result, ensure_ascii=False)
