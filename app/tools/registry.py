"""工具注册表：按名字注册/查找工具，并生成给模型看的工具清单。

插件化关键：新增工具 = 写一个 Tool 并 register，核心循环零改动。
"""
from __future__ import annotations

import json

from .base import Tool


class ToolNotFoundError(Exception):
    pass


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"duplicate tool name: {tool.name}")
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool:
        try:
            return self._tools[name]
        except KeyError:
            raise ToolNotFoundError(f"unknown tool: {name}") from None

    def names(self) -> list[str]:
        return list(self._tools)

    def __contains__(self, name: str) -> bool:
        return name in self._tools

    def render_prompt(self) -> str:
        """生成注入 system prompt 的工具清单（名字 + 描述 + 参数 schema）。"""
        if not self._tools:
            return "(no tools available)"
        lines = []
        for tool in self._tools.values():
            schema = json.dumps(tool.json_schema(), ensure_ascii=False)
            lines.append(f"- {tool.name}: {tool.description}\n  parameters: {schema}")
        return "\n".join(lines)
