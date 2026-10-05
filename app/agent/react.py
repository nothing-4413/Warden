"""自研 ReAct 循环：Thought → Action → Observation，直到 Final Answer。

输出契约（JSON）：模型每轮只能输出一个 JSON 对象，二选一：
  {"thought": "...", "action": "tool_name", "action_input": {...}}
  {"thought": "...", "final_answer": "..."}

选 JSON 而非经典自由文本 "Action:" 解析的原因：
- 本地小模型（Ollama/Qwen）对 JSON 指令的遵从度高于自由格式
- 解析鲁棒（去代码围栏后 json.loads），无需脆弱的正则
"""

from __future__ import annotations

from ..harness import RunRecord, with_retry
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

{fewshot}
"""

_FEWSHOT = """\
Example (reference only; do not echo it verbatim):

User: What is 12 * 34?
{"thought": "I need to multiply, so I will call the calculator.", "action": "calculator", "action_input": {"expression": "12 * 34"}}
Observation: 408
{"thought": "The calculator returned 408.", "final_answer": "12 * 34 = 408"}
"""


_RETRY_JSON_PROMPT = (
    "Your previous reply was not valid JSON. "
    "Reply with EXACTLY ONE valid JSON object — no code fences, no extra text."
)

_FORCE_FINAL_PROMPT = (
    "You have reached the step limit. Based on the observations above, "
    "give the best final answer you can, as plain text."
)

_REFLECT_PROMPT = """\
Critically review this draft final answer against the user's request and the observations.
Reply with EXACTLY ONE JSON object:
{"verdict": "ok" | "redo", "feedback": "..."}

Use "ok" if the answer is complete, correct, and well-supported; use "redo" if it has a
gap, error, or missing step — then put a concrete instruction for improvement in "feedback".
"""


class ReactAgent(BaseAgent):
    name = "react"

    def _system_prompt(self) -> str:
        return _SYSTEM_TEMPLATE.format(tools=self.tools.render_prompt(), fewshot=_FEWSHOT)

    def _messages(self, history: list[dict]) -> list[dict]:
        return [{"role": "system", "content": self._system_prompt()}, *history]

    def _chat(self, messages: list[dict]) -> str:
        return with_retry(
            lambda: self.llm.chat(messages),
            attempts=self.settings.retry_attempts,
            backoff_s=self.settings.retry_backoff_s,
        )

    def _chat_json(self, messages: list[dict]) -> tuple[dict | None, str]:
        """chat 后解析 JSON；解析失败时把错误反馈回模型自纠一次。

        返回 (parsed, raw)：自纠仍失败时 parsed 为 None、raw 为最后一次原始输出。
        """
        raw = self._chat(messages)
        try:
            return extract_json(raw), raw
        except ValueError:
            pass
        messages.append({"role": "assistant", "content": raw.strip()})
        messages.append({"role": "user", "content": _RETRY_JSON_PROMPT})
        raw = self._chat(messages)
        try:
            return extract_json(raw), raw
        except ValueError:
            return None, raw

    def _reflect(self, draft: str, steps: list[AgentStep]) -> tuple[str, str]:
        """自反思（Reflexion-lite）：让模型审查草稿答案，返回 (verdict, feedback)。

        verdict ∈ {"ok","redo"}；反思解析失败时默认 "ok" 放行，不阻塞主循环。
        """
        observations = "\n".join(
            f"- {s.action or '(think)'}: {s.observation}"
            for s in steps
            if s.observation is not None
        )
        parsed, _raw = self._chat_json(
            [
                {"role": "system", "content": _REFLECT_PROMPT},
                {
                    "role": "user",
                    "content": f"Draft answer:\n{draft}\n\nObservations so far:\n{observations}",
                },
            ]
        )
        if parsed is None:
            return "ok", ""
        verdict = str(parsed.get("verdict", "ok")).lower()
        feedback = str(parsed.get("feedback", "") or "")
        return (verdict if verdict in ("ok", "redo") else "ok"), feedback

    def _run(self, history: list[dict], record: RunRecord | None) -> AgentRunResult:
        return self._loop(self._messages(history), [], record, 0)

    def _resume(self, record: RunRecord) -> AgentRunResult:
        # 用已持久化的步骤重建上下文，然后从中断处继续
        messages = self._messages(record.input or [])
        steps: list[AgentStep] = []
        for d in record.steps:
            s = AgentStep(
                index=d["index"],
                thought=d.get("thought"),
                action=d.get("action"),
                action_input=d.get("action_input"),
                observation=d.get("observation"),
            )
            steps.append(s)
            if d.get("raw"):
                messages.append({"role": "assistant", "content": d["raw"]})
            if d.get("action"):
                messages.append({"role": "user", "content": f"Observation: {d.get('observation')}"})
        return self._loop(messages, steps, record, len(steps))

    def _loop(
        self,
        messages: list[dict],
        steps: list[AgentStep],
        record: RunRecord | None,
        start_index: int,
    ) -> AgentRunResult:
        for index in range(start_index, self.settings.agent_max_steps):
            parsed, raw = self._chat_json(messages)

            if parsed is None:
                # 自纠后仍解析不出 JSON：把原始文本当最终回答，避免死循环
                answer = raw.strip()
                s = AgentStep(index=index, thought=None, observation=answer)
                steps.append(s)
                self._persist_step(record, s, raw)
                return AgentRunResult(
                    answer=answer, steps=steps, agent=self.name, model=self.llm.model
                )

            if "final_answer" in parsed:
                answer = str(parsed["final_answer"])
                s = AgentStep(index=index, thought=parsed.get("thought"), observation=answer)
                steps.append(s)
                self._persist_step(record, s, raw)

                # 自反思（Reflexion-lite）：定稿前自我校验，必要时带着反馈回炉重答
                if self.settings.reflect_enabled:
                    verdict, feedback = self._reflect(answer, steps)
                    if verdict == "redo":
                        steps.append(
                            AgentStep(
                                index=index,
                                thought="reflection",
                                action="_reflect",
                                observation=feedback,
                            )
                        )
                        self._persist_step(record, steps[-1], None)
                        messages.append({"role": "assistant", "content": raw.strip()})
                        messages.append({"role": "user", "content": f"Reflection: {feedback}"})
                        continue  # 下一轮让模型基于反馈改进答案或补查工具

                return AgentRunResult(
                    answer=answer, steps=steps, agent=self.name, model=self.llm.model
                )

            action = parsed.get("action")
            action_input = parsed.get("action_input") or {}
            if not action:
                # 既没 action 也没 final_answer：把原始文本当最终回答，避免死循环
                answer = raw.strip()
                s = AgentStep(index=index, thought=parsed.get("thought"), observation=answer)
                steps.append(s)
                self._persist_step(record, s, raw)
                return AgentRunResult(
                    answer=answer, steps=steps, agent=self.name, model=self.llm.model
                )

            observation = self._execute(action, action_input)
            s = AgentStep(
                index=index,
                thought=parsed.get("thought"),
                action=action,
                action_input=action_input,
                observation=observation,
            )
            steps.append(s)
            self._persist_step(record, s, raw)

            # 把本轮"模型输出 + 观测"追加回上下文，供下一轮参考
            messages.append({"role": "assistant", "content": raw.strip()})
            messages.append({"role": "user", "content": f"Observation: {observation}"})

        # 达到最大步数：强制收尾 —— 让模型基于已有观测给出最终回答
        try:
            final = self._chat(
                messages + [{"role": "user", "content": _FORCE_FINAL_PROMPT}]
            ).strip()
        except Exception:
            last = steps[-1].observation if steps else "(none)"
            final = (
                f"reached max steps ({self.settings.agent_max_steps}) without a "
                f"final answer; last observation: {last}"
            )
        return AgentRunResult(answer=final, steps=steps, agent=self.name, model=self.llm.model)
