"""CriticPipeline：多 Agent 接力协作（Phase D）。

Orchestrator（M4）是"路由"——一个 LLM 选一个专家、单跳转发；
这里更进一步：多个专家 Agent 依次接力，前一个的产出是后一个的输入，
共同把一个任务打磨到位。

流水线：Researcher（带工具，检索/查证）→ Critic（无工具，挑错补缺并给出改进版最终答案）。
"""

from __future__ import annotations

from .base import AgentRunResult, BaseAgent

_CRITIC_INSTRUCTION = (
    "You are a critical reviewer. Point out factual gaps, unsupported claims, or "
    "missing context in the research findings below, then write an improved final "
    "answer that incorporates your corrections."
)


class CriticPipeline:
    """Researcher → Critic：先检索产出 findings，再由 critic 审查后给出改进版最终答案。"""

    def __init__(self, researcher: BaseAgent, critic: BaseAgent) -> None:
        self.researcher = researcher
        self.critic = critic

    def run(self, question: str) -> AgentRunResult:
        research = self.researcher.run([{"role": "user", "content": question}])
        findings = research.answer

        # 用 f-string 拼接而非 .format()：findings 是模型/用户文本，可能含 {} 破坏 format
        critic_prompt = (
            f"Question: {question}\n\nResearch findings:\n{findings}\n\n{_CRITIC_INSTRUCTION}"
        )
        review = self.critic.run([{"role": "user", "content": critic_prompt}])

        return AgentRunResult(
            answer=review.answer,
            steps=research.steps + review.steps,
            agent="critic_pipeline",
            model=self.researcher.llm.model,
        )
