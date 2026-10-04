"""工具层：Tool 定义、注册表、内置示例工具。"""
from __future__ import annotations

from .base import Tool
from .builtin import calculator_tool, datetime_tool
from .registry import ToolNotFoundError, ToolRegistry

__all__ = [
    "Tool",
    "ToolRegistry",
    "ToolNotFoundError",
    "calculator_tool",
    "datetime_tool",
]


def build_default_registry() -> ToolRegistry:
    """M0 默认注册的示例工具。后续新增工具在此追加即可。"""
    registry = ToolRegistry()
    registry.register(calculator_tool)
    registry.register(datetime_tool)
    return registry
