"""工具层：Tool 定义、注册表、内置示例工具。"""
from __future__ import annotations

from .base import Tool
from .builtin import calculator_tool, datetime_tool
from .builtin.search_notes import make_search_notes_tool
from .registry import ToolNotFoundError, ToolRegistry

__all__ = [
    "Tool",
    "ToolRegistry",
    "ToolNotFoundError",
    "calculator_tool",
    "datetime_tool",
    "make_search_notes_tool",
]


def build_default_registry(retriever=None, top_k: int = 4) -> ToolRegistry:
    """默认工具注册表。传入 retriever 时额外注册 search_notes（RAG 记忆）。"""
    registry = ToolRegistry()
    registry.register(calculator_tool)
    registry.register(datetime_tool)
    if retriever is not None:
        registry.register(make_search_notes_tool(retriever, top_k))
    return registry
