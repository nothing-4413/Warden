"""自研原生 Function Calling 循环（M0 补全，展示协议级工具调用）。

与 ReAct（JSON-in-prompt，模型输出 {"action","action_input"}）的区别：
这里把工具以 OpenAI `tools` 协议直接交给模型，模型原生返回 `tool_calls`
（含 tool_call_id 与结构化 arguments），执行后以 `role:"tool"` 回填上下文。
两条路线并存，展示"prompt 级"与"协议级"两种工具调用方式。
"""

from __future__ import annotations

import json

from ..harness import RunRecord, with_retry
from .base import AgentRunResult, AgentStep, BaseAgent

_SYSTEM_TEMPLATE = """\
You are Warden, an autonomous agent with native function calling.
Use the provided tools when they help; otherwise answer directly.

{fewshot}
"""

_FEWSHOT = """\
Example (reference only): if the user asks "what is 12 * 34?", call the `calculator`
tool with arguments {"expression": "12 * 34"}, then answer using the returned result.
If no tool fits the request, answer directly without calling any tool.
"""

_FORCE_FINAL_PROMPT = (
    "You have reached the step limit. Based on the tool results above, "
    "give the best final answer you can, as plain text."
)


class FunctionCallAgent(BaseAgent):
    name = "function_call"

    def _system_prompt(self) -> str:
        return _SYSTEM_TEMPLATE.format(fewshot=_FEWSHOT)

    def _tools_spec(self) -> list[dict]:
        """把 ToolRegistry 渲染成 OpenAI `tools` 协议格式。"""
        return [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.json_schema(),
                },
            }
            for t in self.tools.all()
        ]

    def _chat(self, messages: list[dict]) -> str:
        return with_retry(
            lambda: self.llm.chat(messages),
            attempts=self.settings.retry_attempts,
            backoff_s=self.settings.retry_backoff_s,
        )

    def _chat_with_tools(self, messages: list[dict]):
        return with_retry(
            lambda: self.llm.chat_with_tools(messages, self._tools_spec()),
            attempts=self.settings.retry_attempts,
            backoff_s=self.settings.retry_backoff_s,
        )

    def _messages(self, history: list[dict]) -> list[dict]:
        return [{"role": "system", "content": self._system_prompt()}, *history]

    def _run(self, history: list[dict], record: RunRecord | None) -> AgentRunResult:
        messages = self._messages(history)
        if record is not None:
            # 把 messages 存进 meta（同一引用随循环增长），供 resume 原样重建 native 上下文
            record.meta = {"messages": messages}
            self.store.update(record)
        return self._loop(messages, [], record, 0)

    def _resume(self, record: RunRecord) -> AgentRunResult:
        messages = (record.meta or {}).get("messages") or self._messages(record.input or [])
        steps = [
            AgentStep(
                index=d["index"],
                thought=d.get("thought"),
                action=d.get("action"),
                action_input=d.get("action_input"),
                observation=d.get("observation"),
            )
            for d in record.steps
        ]
        return self._loop(messages, steps, record, len(steps))

    def _loop(
        self,
        messages: list[dict],
        steps: list[AgentStep],
        record: RunRecord | None,
        start_index: int,
    ) -> AgentRunResult:
        for index in range(start_index, self.settings.agent_max_steps):
            content, calls = self._chat_with_tools(messages)

            if not calls:
                # 模型直接给最终回答（无工具调用）
                answer = (content or "").strip()
                s = AgentStep(index=index, thought=answer, observation=answer)
                steps.append(s)
                self._persist_step(record, s, None)
                return AgentRunResult(
                    answer=answer, steps=steps, agent=self.name, model=self.llm.model
                )

            # 有工具调用：回填 assistant 的 tool_calls 消息，再逐条执行、以 role:"tool" 回填
            messages.append(
                {
                    "role": "assistant",
                    "content": content or None,
                    "tool_calls": [
                        {
                            "id": c.id,
                            "type": "function",
                            "function": {
                                "name": c.name,
                                "arguments": json.dumps(c.arguments, ensure_ascii=False),
                            },
                        }
                        for c in calls
                    ],
                }
            )
            for c in calls:
                observation = self._execute(c.name, c.arguments)
                obs_text = (
                    observation
                    if isinstance(observation, str)
                    else json.dumps(observation, ensure_ascii=False, default=str)
                )
                messages.append({"role": "tool", "tool_call_id": c.id, "content": obs_text})
                s = AgentStep(
                    index=index,
                    thought=None,
                    action=c.name,
                    action_input=c.arguments,
                    observation=observation,
                )
                steps.append(s)
                self._persist_step(record, s, None)

        # 达到最大步数：强制收尾
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
