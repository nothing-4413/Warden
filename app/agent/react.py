"""自研 ReAct 循环：Thought → Action → Observation，直到 Final Answer。

输出契约（JSON）：模型每轮只能输出一个 JSON 对象，二选一：
  {"thought": "...", "action": "tool_name", "action_input": {...}}
  {"thought": "...", "final_answer": "..."}

选 JSON 而非经典自由文本 "Action:" 解析的原因：
- 本地小模型（Ollama/Qwen）对 JSON 指令的遵从度高于自由格式
- 解析鲁棒（去代码围栏后 json.loads），无需脆弱的正则
"""
from __future__ import annotations

from typing import Any

from ..config import Settings
from ..harness import RunRecord, with_retry
from ..llm import LLMClient
from ..tools import ToolRegistry
from .base import AgentRunResult, AgentStep, BaseAgent, extract_json


_SYSTEM_TEMPLATE = """\
You are Warden, an autonomous agent. Solve the user's request step by step.

You may use these tools:
{tools}

Reply with EXACTLY ONE JSON object per turn, no extra text. Two forms are allowed:

1. Call a tool:
{{"thought": "your reasoning", "action": "tool_name", "action_input": {{...}}}}

2. Finish with the final answer:
{{"thought": "your reasoning", "final_answer": "your answer"}}

Rules:
- Think first, then act. Use tools when they help; otherwise answer directly.
- action_input must be a JSON object whose keys match the tool's parameters.
- When you are done, output the final_answer form.
"""


class ReactAgent(BaseAgent):
    name = "react"

    def _system_prompt(self) -> str:
        return _SYSTEM_TEMPLATE.format(tools=self.tools.render_prompt())

    def _messages(self, history: list[dict]) -> list[dict]:
        return [{"role": "system", "content": self._system_prompt()}, *history]

    def _chat(self, messages: list[dict]) -> str:
        return with_retry(
            lambda: self.llm.chat(messages),
            attempts=self.settings.retry_attempts,
            backoff_s=self.settings.retry_backoff_s,
        )

    def _run(self, history: list[dict], record: RunRecord | None) -> AgentRunResult:
        return self._loop(self._messages(history), [], record, 0)

    def _resume(self, record: RunRecord) -> AgentRunResult:
        # 用已持久化的步骤重建上下文，然后从中断处继续
        messages = self._messages(record.input or [])
        steps: list[AgentStep] = []
        for d in record.steps:
            s = AgentStep(index=d["index"], thought=d.get("thought"), action=d.get("action"),
                          action_input=d.get("action_input"), observation=d.get("observation"))
            steps.append(s)
            if d.get("raw"):
                messages.append({"role": "assistant", "content": d["raw"]})
            if d.get("action"):
                messages.append({"role": "user", "content": f"Observation: {d.get('observation')}"})
        return self._loop(messages, steps, record, len(steps))

    def _loop(self, messages: list[dict], steps: list[AgentStep],
              record: RunRecord | None, start_index: int) -> AgentRunResult:
        for index in range(start_index, self.settings.agent_max_steps):
            raw = self._chat(messages)
            parsed = extract_json(raw)

            if "final_answer" in parsed:
                s = AgentStep(index=index, thought=parsed.get("thought"),
                              observation=parsed["final_answer"])
                steps.append(s)
                self._persist_step(record, s, raw)
                return AgentRunResult(answer=str(parsed["final_answer"]), steps=steps,
                                      agent=self.name, model=self.llm.model)

            action = parsed.get("action")
            action_input = parsed.get("action_input") or {}
            if not action:
                # 既没 action 也没 final_answer：把原始文本当最终回答，避免死循环
                answer = raw.strip()
                s = AgentStep(index=index, thought=parsed.get("thought"), observation=answer)
                steps.append(s)
                self._persist_step(record, s, raw)
                return AgentRunResult(answer=answer, steps=steps,
                                      agent=self.name, model=self.llm.model)

            observation = self._execute(action, action_input)
            s = AgentStep(index=index, thought=parsed.get("thought"), action=action,
                          action_input=action_input, observation=observation)
            steps.append(s)
            self._persist_step(record, s, raw)

            # 把本轮"模型输出 + 观测"追加回上下文，供下一轮参考
            messages.append({"role": "assistant", "content": raw.strip()})
            messages.append({"role": "user", "content": f"Observation: {observation}"})

        # 达到最大步数仍未给出 final_answer
        last = steps[-1].observation if steps else "(none)"
        answer = (
            f"reached max steps ({self.settings.agent_max_steps}) without a final "
            f"answer; last observation: {last}"
        )
        return AgentRunResult(answer=answer, steps=steps,
                              agent=self.name, model=self.llm.model)
