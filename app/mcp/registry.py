"""把配置里的 MCP server 全部拉起、适配成 Warden 工具注册表。"""
from __future__ import annotations

from ..tools import ToolRegistry
from .client import MCPClient
from .tools import adapt_mcp_tool


def build_mcp_registry(servers: list[dict]) -> tuple[ToolRegistry, list[MCPClient]]:
    """servers: [{name, command: [...]}]。

    返回 (注册表, 客户端列表)：注册表可挂到 Agent 的 tools；客户端列表由调用方负责 close。
    """
    registry = ToolRegistry()
    clients: list[MCPClient] = []
    for s in servers:
        name = s.get("name", "")
        client = MCPClient(s["command"])
        clients.append(client)
        prefix = f"{name}_" if name else ""
        for t in client.list_tools():
            registry.register(adapt_mcp_tool(client, t.name, t.description, t.input_schema, prefix))
    return registry, clients
