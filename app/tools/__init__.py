"""工具层：Tool 定义、注册表、内置示例工具。"""
from __future__ import annotations

from .base import Tool
from .builtin import calculator_tool, datetime_tool
from .builtin.save_note import make_save_note_tool
from .builtin.search_notes import make_search_notes_tool
from .registry import ToolNotFoundError, ToolRegistry

__all__ = [
    "Tool",
    "ToolRegistry",
    "ToolNotFoundError",
    "calculator_tool",
    "datetime_tool",
    "make_search_notes_tool",
    "make_save_note_tool",
]


def build_default_registry(retriever=None, top_k: int = 4,
                           min_score: float = 0.0, indexer=None) -> ToolRegistry:
    """默认工具注册表。

    传入 retriever 时额外注册 search_notes（RAG 记忆的"读"）；
    传入 indexer 时额外注册 save_note（RAG 记忆的"写"）。二者可独立开关。
    """
    registry = ToolRegistry()
    registry.register(calculator_tool)
    registry.register(datetime_tool)
    if retriever is not None:
        registry.register(make_search_notes_tool(retriever, top_k, min_score))
    if indexer is not None:
        registry.register(make_save_note_tool(indexer))
    return registry
