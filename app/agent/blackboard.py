"""Blackboard：多 Agent 共享工作记忆（黑板模式）。

相比 CriticPipeline（固定线性流水线：前一个 Agent 的产出直接塞进后一个的 prompt），
黑板是"共享状态"——每个 Agent 通过 blackboard_write / blackboard_read 工具主动、
按需读写一块公共键值黑板，多个 Agent 解耦协作：写方不必知道谁会读，读方也不必
依赖写方的返回结构。可扩展为更多 Agent（含异步）共享同一块黑板。

Blackboard = 线程安全的共享字典；BlackboardTeam = 顺序执行一组 (agent, role) 的协调器。
"""
from __future__ import annotations

import threading

from .base import AgentRunResult, AgentStep, BaseAgent


class Blackboard:
    """线程安全的共享键值黑板（value 为字符串）。"""

    def __init__(self) -> None:
        self._data: dict[str, str] = {}
        self._lock = threading.Lock()

    def write(self, key: str, value: str) -> None:
        with self._lock:
            self._data[key] = value

    def read(self, key: str, default: str | None = None) -> str | None:
        with self._lock:
            return self._data.get(key, default)

    def keys(self) -> list[str]:
        with self._lock:
            return list(self._data.keys())

    def snapshot(self) -> dict[str, str]:
        """整块黑板的副本（用于展示/调试/持久化）。"""
        with self._lock:
            return dict(self._data)


class BlackboardTeam:
    """顺序执行多个 Agent，共享同一块黑板。

    add(agent, role) 注册一个成员及其角色指令；run(question) 依次执行：
    第一个成员拿到原始问题 + 角色指令；后续成员额外被告知"先读黑板再继续"。
    返回最后一个成员的答案，steps 合并所有成员（全程可追溯）。
    """

    def __init__(self, blackboard: Blackboard | None = None) -> None:
        self.blackboard = blackboard or Blackboard()
        self._members: list[tuple[BaseAgent, str]] = []

    def add(self, agent: BaseAgent, role: str) -> None:
        self._members.append((agent, role))

    def run(self, question: str) -> AgentRunResult:
        if not self._members:
            raise ValueError("BlackboardTeam needs at least one member")
        all_steps: list[AgentStep] = []
        result: AgentRunResult | None = None
        for i, (agent, role) in enumerate(self._members):
            if i == 0:
                prompt = f"{question}\n\n{role}"
            else:
                prompt = (
                    f"Task: {question}\n\n"
                    f"{role}\n\n"
                    f"Earlier agents wrote their contributions to the shared blackboard. "
                    f"Read what you need with the blackboard_read tool, then continue."
                )
            result = agent.run([{"role": "user", "content": prompt}])
            all_steps += result.steps
        return AgentRunResult(
            answer=result.answer,
            steps=all_steps,
            agent="blackboard_team",
            model=self._members[0][0].llm.model,
        )
