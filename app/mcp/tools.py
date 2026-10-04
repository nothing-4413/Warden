"""把 MCP 工具适配成 Warden 的 Tool（挂进 ToolRegistry，Agent 即可调用）。"""
from __future__ import annotations

from typing import Optional

from pydantic import Field, create_model

from ..tools.base import Tool
from .client import MCPClient

_TYPE_MAP = {
    "string": str,
    "number": float,
    "integer": int,
    "boolean": bool,
    "array": list,
    "object": dict,
}


def build_input_model(name: str, input_schema: dict):
    """用 MCP 工具的 inputSchema 动态生成 pydantic 输入模型。

    类型映射是启发式的（string/number/integer/boolean/array/object），
    required 里的字段必填，其余 Optional 且默认 None（调用时过滤掉）。
    """
    properties = input_schema.get("properties", {}) or {}
    required = set(input_schema.get("required", []) or [])
    fields = {}
    for key, spec in properties.items():
        t = _TYPE_MAP.get(spec.get("type"), str)  # 未知类型回退 str
        if key in required:
            fields[key] = (t, Field(description=spec.get("description", "")))
        else:
            fields[key] = (Optional[t], Field(default=None, description=spec.get("description", "")))
    return create_model(f"MCP_{name}", **fields)


def adapt_mcp_tool(client: MCPClient, mcp_name: str, mcp_description: str,
                   input_schema: dict, prefix: str = "") -> Tool:
    """把一个 MCP 工具包装成 Warden Tool；调用时透传给 client.call_tool。"""
    model = build_input_model(mcp_name, input_schema)

    def _call(**kwargs):
        args = {k: v for k, v in kwargs.items() if v is not None}
        return client.call_tool(mcp_name, args)

    name = (prefix + mcp_name) if prefix else mcp_name
    return Tool(
        name=name,
        description=mcp_description or f"MCP tool {mcp_name}",
        input_model=model,
        func=_call,
    )
