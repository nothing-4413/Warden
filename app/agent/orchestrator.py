"""Orchestrator：多 Agent 协调器（M4）。

一个"路由"LLM 决定把请求交给哪个专家（specialist）；专家各自有独立工具注册表
（如 researcher 带 search_notes / MCP 工具，writer 纯生成）。路由只转发不执行，
专家复用 M0 的 ReactAgent/PlanActAgent（通过 name/description 参数定制身份）。
"""
from __future__ import annotations

from ..llm import LLMClient
from .base import AgentRunResult, BaseAgent, extract_json

_ROUTER_SYSTEM = (
    "You are a router in a multi-agent system. Given the user request, pick the single "
    "best specialist and rewrite the request as one focused task for that specialist.\n"
    "Reply with EXACTLY ONE JSON object: {{\"specialist\": \"<name>\", \"task\": \"<focused task>\"}}.\n"
    "Available specialists:\n{roster}"
)


class Orchestrator:
    def __init__(self, specialists: dict[str, BaseAgent], llm: LLMClient) -> None:
        if not specialists:
            raise ValueError("Orchestrator needs at least one specialist")
        self.specialists = specialists
        self.llm = llm

    def _roster(self) -> str:
        return "\n".join(f"- {name}: {a.description or a.name}"
                         for name, a in self.specialists.items())

    def run(self, history: list[dict]) -> AgentRunResult:
        request = history[-1]["content"] if history else ""
        route = extract_json(self.llm.chat([
            {"role": "system", "content": _ROUTER_SYSTEM.format(roster=self._roster())},
            *history,
        ]))
        name = route.get("specialist")
        task = route.get("task") or request
        # 未知专家（或模型没给）→ 回退到第一个，保证总能出结果
        agent = self.specialists.get(name) or next(iter(self.specialists.values()))
        return agent.run([{"role": "user", "content": task}])
