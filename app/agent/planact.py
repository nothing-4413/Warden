"""自研 PlanAct 循环：先规划 → 逐步执行 → 汇总。

三步：
1. Plan —— 模型把任务拆成有序步骤列表（JSON），每步可选绑定一个工具+参数
2. Act —— 按顺序执行每步（复用 BaseAgent._execute）；无工具的步骤标记为推理步
3. Summarize —— 汇总所有步骤结果，产出最终答案

设计取舍：M0 用"规划期即固定工具调用"的确定性执行（不逐步二次推理），
可解释、可控、无额外 LLM 调用；逐步二次推理留作后续扩展点。
断点续跑：plan 与已完成步骤都落库，resume 时跳过已执行步骤、直接接着 Act/Summarize。
"""
from __future__ import annotations

import json
from typing import Any

from ..config import Settings
from ..harness import RunRecord, with_retry
from ..llm import LLMClient
from ..tools import ToolRegistry
from .base import AgentRunResult, AgentStep, BaseAgent, extract_json


_PLAN_TEMPLATE = """\
You are Warden, a planning agent. Break the user's request into an ordered list of concrete steps.

Available tools:
{tools}

Reply with EXACTLY ONE JSON object:
{{"plan": [{{"step": "what to do", "tool": "tool_name or null", "args": {{...}} or null}}]}}

Rules:
- Each step is atomic. Use a tool when one helps; otherwise set tool=null (a reasoning step).
- Order matters: later steps may depend on earlier results.
- Keep the plan as short as possible.
"""

_SUMMARIZE_TEMPLATE = """\
You executed a plan. Summarize the results into a final answer for the user.

Original request:
{request}

Step results (JSON):
{results}

Reply with the final answer as plain text.
"""


class PlanActAgent(BaseAgent):
    name = "planact"

    def _chat(self, messages: list[dict]) -> str:
        return with_retry(
            lambda: self.llm.chat(messages),
            attempts=self.settings.retry_attempts,
            backoff_s=self.settings.retry_backoff_s,
        )

    def _run(self, history: list[dict], record: RunRecord | None) -> AgentRunResult:
        request = history[-1]["content"]

        # 1. Plan
        plan_messages = [
            {"role": "system", "content": _PLAN_TEMPLATE.format(tools=self.tools.render_prompt())},
            *history,
        ]
        plan_raw = self._chat(plan_messages)
        plan = extract_json(plan_raw).get("plan") or []
        if record is not None:
            record.meta = {"plan": plan, "request": request}
            self.store.update(record)
        return self._execute_plan(request, plan, record, 0)

    def _resume(self, record: RunRecord) -> AgentRunResult:
        meta = record.meta or {}
        plan = meta.get("plan") or []
        request = meta.get("request") or ""
        # 已完成的步骤数 = len(record.steps)，从那里接着执行
        return self._execute_plan(request, plan, record, len(record.steps))

    def _execute_plan(self, request: str, plan: list, record: RunRecord | None,
                      start_index: int) -> AgentRunResult:
        done = record.steps if record is not None else []
        steps = [AgentStep(index=d["index"], thought=d.get("step"), action=d.get("tool"),
                           action_input=d.get("args"), observation=d.get("result")) for d in done]
        results = [{"step": d.get("step"), "result": d.get("result")} for d in done]

        # 2. Act（从 start_index 继续，支持断点续跑）
        for i in range(start_index, len(plan)):
            item = plan[i]
            step_desc = item.get("step", "")
            tool = item.get("tool")
            args = item.get("args") or {}
            if tool:
                observation = self._execute(tool, args)
                steps.append(AgentStep(index=i, thought=step_desc, action=tool,
                                       action_input=args, observation=observation))
                results.append({"step": step_desc, "result": observation})
            else:
                # 无工具的纯推理步骤：不额外调用 LLM，交由汇总阶段处理
                steps.append(AgentStep(index=i, thought=step_desc))
                results.append({"step": step_desc, "result": "(reasoning step)"})
            if record is not None:
                record.steps.append({"index": i, "step": step_desc, "tool": tool,
                                     "args": args, "result": results[-1]["result"]})
                self.store.update(record)

        # 3. Summarize
        summary_messages = [
            {"role": "system", "content": "You are Warden, summarizing an executed plan."},
            {"role": "user", "content": _SUMMARIZE_TEMPLATE.format(
                request=request, results=json.dumps(results, ensure_ascii=False))},
        ]
        answer = self._chat(summary_messages).strip()

        return AgentRunResult(answer=answer, steps=steps, agent=self.name, model=self.llm.model)
