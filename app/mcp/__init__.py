"""MCP（Model Context Protocol）客户端 + 工具适配（M4）。"""
from .client import MCPClient, MCPError, MCPTool
from .registry import build_mcp_registry
from .tools import adapt_mcp_tool, build_input_model

__all__ = [
    "MCPClient",
    "MCPError",
    "MCPTool",
    "adapt_mcp_tool",
    "build_input_model",
    "build_mcp_registry",
]
