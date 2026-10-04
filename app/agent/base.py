"""Agent 基类、运行结果数据结构、共享的 JSON 解析工具。"""
from __future__ import annotations

import json
import re
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from ..config import Settings
from ..harness import RunRecord, RunStore, STATUS_ERROR, STATUS_OK, new_trace_id
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

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "thought": self.thought,
            "action": self.action,
            "action_input": self.action_input,
            "observation": self.observation,
        }


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
    传入 store 后，run/resume 会持久化 RunRecord（trace_id = run id），支持断点续跑。
    """

    name: str = "base"

    def __init__(self, settings: Settings, llm: LLMClient, tools: ToolRegistry,
                 store: RunStore | None = None) -> None:
        self.settings = settings
        self.llm = llm
        self.tools = tools
        self.store = store

    # ---- 对外入口：持久化 + 状态收尾 ----
    def run(self, history: list[dict], trace_id: str | None = None) -> AgentRunResult:
        record = self._begin("chat", history, trace_id)
        try:
            result = self._run(history, record)
            self._finish_ok(record, result)
            return result
        except Exception as exc:
            self._finish_error(record, exc)
            raise

    def resume(self, run_id: str) -> AgentRunResult:
        """从已持久化的断点继续（断点续跑）。"""
        record = self._load_run(run_id)
        try:
            result = self._resume(record)
            self._finish_ok(record, result)
            return result
        except Exception as exc:
            self._finish_error(record, exc)
            raise

    @abstractmethod
    def _run(self, history: list[dict], record: RunRecord | None) -> AgentRunResult:
        ...

    @abstractmethod
    def _resume(self, record: RunRecord) -> AgentRunResult:
        ...

    # ---- 持久化辅助 ----
    def _begin(self, kind: str, payload: Any, trace_id: str | None) -> RunRecord | None:
        if self.store is None:
            return None
        record = RunRecord(id=trace_id or new_trace_id(), kind=kind, name=self.name, input=payload)
        self.store.start(record)
        return record

    def _finish_ok(self, record: RunRecord | None, result: AgentRunResult) -> None:
        if record is not None:
            record.status = STATUS_OK
            record.output = result.answer
            record.finished_at = time.time()
            self.store.update(record)

    def _finish_error(self, record: RunRecord | None, exc: Exception) -> None:
        if record is not None:
            record.status = STATUS_ERROR
            record.error = f"{type(exc).__name__}: {exc}"
            record.finished_at = time.time()
            self.store.update(record)

    def _load_run(self, run_id: str) -> RunRecord:
        if self.store is None:
            raise ValueError("resume requires a RunStore")
        record = self.store.get(run_id)
        if record is None:
            raise ValueError(f"unknown run: {run_id}")
        if record.kind != "chat" or record.name != self.name:
            raise ValueError(f"run {run_id} is not a {self.name} chat run")
        return record

    def _persist_step(self, record: RunRecord | None, step: AgentStep,
                      raw: str | None = None) -> None:
        """把一步追加进 RunRecord.steps 并落库。raw 是模型原始输出（resume 重建上下文用）。"""
        if record is not None:
            d = step.to_dict()
            if raw is not None:
                d["raw"] = raw
            record.steps.append(d)
            self.store.update(record)

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
