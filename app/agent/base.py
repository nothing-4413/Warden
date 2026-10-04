"""Agent 基类、运行结果数据结构、共享的 JSON 解析工具。"""
from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from ..config import Settings
from ..llm import LLMClient
from ..tools import ToolNotFoundError, ToolRegistry


@dataclass
class AgentStep:
    """循环里的一步：模型思考 + （可选）工具调用 + 观测结果。"""
    index: int
    thought: str | None = None
    action: str | None = None
    action_input: dict[str, Any] | None = None
    observation: Any = None


@dataclass
class AgentRunResult:
    answer: str
    steps: list[AgentStep] = field(default_factory=list)
    agent: str = ""
    model: str = ""


_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)


def extract_json(text: str) -> dict[str, Any]:
    """从模型输出里鲁棒地取出 JSON 对象。容忍代码围栏与前后噪声。"""
    text = text.strip()
    m = _FENCE_RE.search(text)
    if m:
        text = m.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # 退而求其次：取第一个 { 到最后一个 } 之间
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            pass
    raise ValueError(f"could not parse JSON from model output: {text[:300]!r}")


class BaseAgent(ABC):
    """所有 Agent 循环的统一入口：run(history) -> AgentRunResult。

    history 是对话历史，元素 {"role","content"}；最后一条视为当前用户指令。
    """

    name: str = "base"

    def __init__(self, settings: Settings, llm: LLMClient, tools: ToolRegistry) -> None:
        self.settings = settings
        self.llm = llm
        self.tools = tools

    @abstractmethod
    def run(self, history: list[dict]) -> AgentRunResult:
        ...

    def _execute(self, action: str, action_input: dict[str, Any]) -> Any:
        """查工具并执行；错误以字符串形式回传（让模型看到后自纠）。"""
        try:
            tool = self.tools.get(action)
        except ToolNotFoundError:
            return f"error: unknown tool '{action}'. Available: {self.tools.names()}"
        try:
            return tool.run(action_input)
        except Exception as exc:  # 工具内部报错回传给模型
            return f"error: {type(exc).__name__}: {exc}"
