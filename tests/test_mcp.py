"""M4 Part1：MCP 客户端（stdio JSON-RPC）+ 工具适配。"""

import sys

import pytest
from pydantic import ValidationError

from app.mcp.client import MCPClient
from app.mcp.tools import adapt_mcp_tool, build_input_model

# 内联假 MCP server：真实走 stdio 协议，按 method 回包（print 必须 flush 避免管道缓冲阻塞）
_FAKE_SERVER = """
import sys, json
for line in sys.stdin:
    msg = json.loads(line)
    m = msg.get("method")
    if m == "initialize":
        print(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": {
            "protocolVersion": "2024-11-05",
            "serverInfo": {"name": "fake", "version": "1"},
            "capabilities": {"tools": {}}}}), flush=True)
    elif m == "notifications/initialized":
        pass
    elif m == "tools/list":
        print(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": {"tools": [{
            "name": "echo",
            "description": "echo the text back",
            "inputSchema": {"type": "object",
                            "properties": {"text": {"type": "string"},
                                           "times": {"type": "integer"}},
                            "required": ["text"]}}]}}), flush=True)
    elif m == "tools/call":
        args = msg["params"]["arguments"]
        print(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": {
            "content": [{"type": "text", "text": "echo:" + args.get("text", "")}]}}), flush=True)
"""


def _make_client():
    return MCPClient([sys.executable, "-c", _FAKE_SERVER])


def test_client_list_tools_and_call():
    client = _make_client()
    try:
        tools = client.list_tools()
        assert len(tools) == 1
        assert tools[0].name == "echo"
        assert client.call_tool("echo", {"text": "hi"}) == "echo:hi"
    finally:
        client.close()


def test_build_input_model_required_and_optional():
    schema = {
        "type": "object",
        "properties": {"text": {"type": "string"}, "times": {"type": "integer"}},
        "required": ["text"],
    }
    model = build_input_model("echo", schema)
    obj = model.model_validate({"text": "hi"})  # times 可选缺省
    assert obj.text == "hi"
    assert obj.times is None
    with pytest.raises(ValidationError):
        model.model_validate({})  # text 必填


def test_adapt_mcp_tool_runs_through_client():
    client = _make_client()
    try:
        tool = adapt_mcp_tool(
            client,
            "echo",
            "echo back",
            {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]},
        )
        assert tool.run({"text": "hello"}) == "echo:hello"
    finally:
        client.close()
