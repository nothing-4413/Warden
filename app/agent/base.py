"""Agent 基类、运行结果数据结构、共享的 JSON 解析工具。"""
from __future__ import annotations

import json
import re
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from ..config import Settings
from ..gateway import derive_session_id, gateway_session_scope
from ..harness import RunRecord, RunStore, STATUS_ERROR, STATUS_OK, new_trace_id, record_run
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
    self_eval: dict[str, Any] | None = None


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
    description: str = ""

    _SELF_EVAL_PROMPT = (
        "You just produced the following answer. Judge how confident you are that it is "
        "correct, and briefly say why.\n\n"
        "Answer:\n<<ANSWER>>\n\n"
        'Reply with a single JSON object: {"confidence": <float 0.0-1.0>, '
        '"reason": "<one sentence>"}'
    )

    def __init__(self, settings: Settings, llm: LLMClient, tools: ToolRegistry,
                 store: RunStore | None = None, name: str | None = None,
                 description: str | None = None) -> None:
        self.settings = settings
        self.llm = llm
        self.tools = tools
        self.store = store
        # 允许按实例定制身份（M4 多 Agent：同一种循环复用作不同专家）
        if name is not None:
            self.name = name
        if description is not None:
            self.description = description

    # ---- 对外入口：持久化 + 状态收尾 ----
    def run(self, history: list[dict], trace_id: str | None = None) -> AgentRunResult:
        # M6：整轮运行（含下面 _history_with_summary 里的摘要压缩调用）都归属同一个
        # 网关会话。会话 id 由**裁剪前**的完整历史派生，所以上下文窗口怎么滑动，
        # InferGate 账本里都还是同一个会话。
        with gateway_session_scope(derive_session_id(history)):
            return self._turn(history, trace_id)

    def _turn(self, history: list[dict], trace_id: str | None) -> AgentRunResult:
        started = time.time()
        # 多轮上下文窗口：超出上限先把最旧消息压成摘要；压缩结果随 record.input 持久化，
        # resume 重建上下文时复用同一份，保证续跑与首跑看到一致的上下文。
        history = self._history_with_summary(history)
        record = self._begin("chat", history, trace_id)
        try:
            result = self._run(history, record)
            self._maybe_self_eval(result)
            self._finish_ok(record, result)
            self._record_metrics("chat", "ok", started)
            return result
        except Exception as exc:
            self._finish_error(record, exc)
            self._record_metrics("chat", "error", started)
            raise

    def resume(self, run_id: str) -> AgentRunResult:
        """从已持久化的断点继续（断点续跑）。"""
        record = self._load_run(run_id)
        # M6：续跑用落库的原始历史派生会话 id，与首跑落在同一个会话里
        history = record.input if isinstance(record.input, list) else []
        with gateway_session_scope(derive_session_id(history)):
            return self._resume_turn(record)

    def _resume_turn(self, record: RunRecord) -> AgentRunResult:
        started = time.time()
        try:
            result = self._resume(record)
            self._maybe_self_eval(result)
            self._finish_ok(record, result)
            self._record_metrics("chat", "ok", started)
            return result
        except Exception as exc:
            self._finish_error(record, exc)
            self._record_metrics("chat", "error", started)
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

    def _record_metrics(self, kind: str, status: str, started: float) -> None:
        """把本次运行结果记入 Prometheus 指标（失败率告警的数据源）。"""
        if self.settings.metrics_enabled:
            record_run(kind, self.name, status, time.time() - started)

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
            return self._truncate_observation(
                f"error: unknown tool '{action}'. Available: {self.tools.names()}")
        try:
            result = tool.run(action_input)
        except Exception as exc:  # 工具内部报错回传给模型
            return self._truncate_observation(f"error: {type(exc).__name__}: {exc}")
        return self._truncate_observation(result)

    def _truncate_observation(self, value: Any) -> Any:
        """截断过长的工具观测，避免撑爆上下文窗口（上下文工程）。

        只截断字符串（长文本都来自字符串结果，如 search_notes/MCP/错误信息）；
        非字符串（如 calculator 返回的数字）保持原样，不影响既有行为。
        """
        limit = self.settings.max_observation_chars
        if limit <= 0 or not isinstance(value, str):
            return value
        if len(value) <= limit:
            return value
        return value[:limit] + f"... [truncated, {len(value)} chars total]"

    def _history_with_summary(self, history: list[dict]) -> list[dict]:
        """多轮上下文窗口：历史超长时把最旧消息压成一段摘要（滑动窗口 + 摘要压缩）。

        保留最近 context_max_messages 条原文；更早的历史用一次 LLM 调用压成 2-3 句
        摘要，作为上下文开头。摘要失败则退化为"纯窗口"（丢弃最旧消息），保证循环
        不被上下文问题卡死。limit <= 0 表示不压缩，原样返回。
        """
        limit = self.settings.context_max_messages
        if limit <= 0 or len(history) <= limit:
            return history
        overflow = history[:-limit]
        kept = history[-limit:]
        try:
            summary = self.llm.chat([
                {"role": "system",
                 "content": "Summarize the conversation below in 2-3 sentences, "
                            "preserving the user's goals, key facts, and decisions."},
                *overflow,
            ]).strip()
        except Exception:
            # 摘要失败（LLM 不可达/超时）就退回纯窗口，宁可丢信息也不阻塞对话
            return kept
        return [{"role": "user",
                 "content": f"[Earlier conversation summary]\n{summary}"}, *kept]

    def _maybe_self_eval(self, result: AgentRunResult) -> None:
        """置信度自评：开启后让模型评估自己答案的可信度，结果挂在 result.self_eval。

        失败静默（评估不了就返回 None），绝不影响主流程；self_eval 已存在则不重复评估。
        """
        if not self.settings.self_eval_enabled or result.self_eval is not None:
            return
        result.self_eval = self._self_eval(result.answer)

    def _self_eval(self, answer: str) -> dict[str, Any] | None:
        """一次 LLM 调用评估答案置信度。解析失败/LLM 不可达返回 None。

        用 replace 而非 .format()：prompt 里含有字面 JSON 花括号 {"confidence"...}，
        .format() 会把它当占位符解析；答案本身也可能含 {}，同样不能走 format。
        """
        try:
            content = self._SELF_EVAL_PROMPT.replace("<<ANSWER>>", answer)
            raw = self.llm.chat([{"role": "user", "content": content}])
            return extract_json(raw)
        except Exception:
            return None
