"""MCP 装配：把配置里的 server 拉起、适配、按前缀注册（用假 client，不真起进程）。"""

from __future__ import annotations

import pytest

import app.mcp.registry as mcp_registry
from app.mcp.registry import build_mcp_registry


class FakeMCPClient:
    """冒充 MCPClient：固定暴露一个 echo 工具。"""

    instances: list[FakeMCPClient] = []

    def __init__(self, command: list[str]) -> None:
        self.command = command
        self.closed = False
        type(self).instances.append(self)

    def list_tools(self):
        class _Tool:
            name = "echo"
            description = "把文本原样返回"
            input_schema = {
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
            }

        return [_Tool()]

    def call_tool(self, name: str, arguments: dict):
        return f"echo:{arguments.get('text', '')}"

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def fake_clients(monkeypatch) -> list[FakeMCPClient]:
    FakeMCPClient.instances = []
    monkeypatch.setattr(mcp_registry, "MCPClient", FakeMCPClient)
    return FakeMCPClient.instances


def test_build_mcp_registry_prefixes_tools_by_server_name(fake_clients) -> None:
    registry, clients = build_mcp_registry(
        [
            {"name": "fs", "command": ["mcp-fs"]},
            {"command": ["mcp-anon"]},  # 没写 name：不加前缀
        ]
    )

    assert [t.name for t in registry.all()] == ["fs_echo", "echo"]
    assert len(clients) == 2
    assert clients[0].command == ["mcp-fs"]  # 命令原样透传
    # 适配出来的工具真的会走回 client
    assert registry.get("fs_echo").run({"text": "hi"}) == "echo:hi"


def test_build_mcp_registry_empty_config(fake_clients) -> None:
    registry, clients = build_mcp_registry([])
    assert registry.all() == [] and clients == []


def test_clients_are_returned_for_caller_to_close(fake_clients) -> None:
    _, clients = build_mcp_registry([{"name": "fs", "command": ["x"]}])
    assert all(not c.closed for c in clients)  # 生命周期交给调用方（CLI / API）
    for c in clients:
        c.close()
    assert all(c.closed for c in clients)
