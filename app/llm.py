"""OpenAI 兼容的 LLM 客户端。只依赖 httpx，直接 POST /chat/completions。

不引入 openai SDK：base_url 指向哪就是哪（OpenAI / Ollama / vLLM / LM Studio），
每行逻辑透明，便于切本地模型。

M6：同一条通路也用来对接 InferGate 网关——带上会话/租户/幂等键，把
409 in_flight 当可重试、把幂等回放当正常成功、把 409 conflict 当键派生的 bug。
端点完全不认识这些头时（裸 OpenAI / Ollama / 旧网关）行为与以前一模一样。
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass

import httpx

from .config import Settings
from .gateway import (
    HEADER_IDEMPOTENCY_KEY,
    HEADER_REPLAY,
    HEADER_REPLAY_AGE,
    HEADER_REPLAY_ORIGIN,
    HEADER_REPLAY_UPSTREAM,
    HEADER_SESSION,
    HEADER_TENANT,
    HEADER_UPSTREAM_NAME,
    CapabilityCache,
    ModelCapability,
    derive_idempotency_key,
    get_capability_cache,
    resolve_session_id,
)
from .harness import (
    compute_cost,
    record_idempotent_replay,
    record_in_flight_retry,
    record_llm_usage,
)

logger = logging.getLogger(__name__)

# InferGate 拒绝"同一个幂等键的并发请求"时返回的错误类型（HTTP 409）
TYPE_IDEMPOTENCY_CONFLICT = "infergate_idempotency_conflict"
TYPE_IDEMPOTENCY_IN_FLIGHT = "infergate_idempotency_in_flight"

# 能力探测限时：上下文窗口是"锦上添花"，绝不能让一次慢探测拖住对话
_CAPABILITY_TIMEOUT_S = 10.0


class LLMError(Exception):
    """LLM 调用失败（网络错误 / 4xx / 5xx）。"""


@dataclass
class Usage:
    """一次 LLM 调用的 token 用量。"""

    prompt_tokens: int = 0
    completion_tokens: int = 0


@dataclass
class ToolCall:
    """模型发起的一次原生工具调用（OpenAI function-calling 协议）。"""

    id: str
    name: str
    arguments: dict


@dataclass
class GatewayOutcome:
    """网关（InferGate）对本次请求的记账信息（M6）。

    不是 InferGate 时全是默认值；replayed=True 就是"这次没有真的打到上游、
    Warden 没有被重复计费"的证据。
    """

    key: str = ""
    session_id: str = ""
    replayed: bool = False
    replay_origin: str = ""
    replay_age_ms: int = 0
    replay_upstream: str = ""
    upstream_name: str = ""
    attempts: int = 1


def _gateway_error_type(resp) -> str:
    """读出 InferGate 错误体里的错误类型（非 409 / 不是 JSON / 别家网关 → 空串）。

    InferGate 用 OpenAI 形状的 {"error": {"message": ..., "type": ...}}；
    这里同时容忍顶层 {"type": ...}，两种写法在真实网关与文档里都出现过。
    任何解析异常都吞掉：解析错误体失败不该比原始 HTTP 错误更严重。
    """
    if getattr(resp, "status_code", None) != 409:
        return ""
    try:
        data = resp.json()
    except Exception:
        return ""
    if not isinstance(data, dict):
        return ""
    err = data.get("error")
    if isinstance(err, dict) and isinstance(err.get("type"), str):
        return err["type"]
    if isinstance(data.get("type"), str):
        return data["type"]
    return ""


def _header_int(headers, name: str) -> int:
    try:
        return int(float(str(headers.get(name, "") or "").strip()))
    except (TypeError, ValueError):
        return 0


def _retry_after_s(resp, default: float) -> float:
    """取 Retry-After 秒数；缺失/不是数字时用配置里的缺省值。"""
    headers = getattr(resp, "headers", None) or {}
    raw = str(headers.get("Retry-After", "") or "").strip()
    try:
        return max(0.0, float(raw))
    except ValueError:
        return max(0.0, float(default))


class LLMClient:
    def __init__(self, settings: Settings, capabilities: CapabilityCache | None = None) -> None:
        self._settings = settings
        self._base_url = settings.llm_base_url.rstrip("/")
        self._api_key = settings.llm_api_key
        self._model = settings.llm_model
        self._temperature = settings.llm_temperature
        self._timeout = settings.llm_timeout_s
        # 能力报告进程内缓存（M6）：默认走全局单例，测试可注入自己的
        self._capabilities = capabilities or get_capability_cache(
            settings.gateway_capabilities_ttl_s
        )
        self.last_usage: Usage | None = None
        # 最近一次调用在网关侧的身份/回放情况（M6）
        self.last_gateway: GatewayOutcome | None = None

    @property
    def model(self) -> str:
        return self._model

    def chat(self, messages: list[dict], temperature: float | None = None) -> str:
        """发送 messages，返回助手文本回复。

        messages 元素形如 {"role": "system"|"user"|"assistant", "content": "..."}。
        """
        payload = {
            "model": self._model,
            "messages": messages,
            "temperature": self._temperature if temperature is None else temperature,
        }
        data = self._post(payload)
        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError(f"unexpected LLM response shape: {data}") from exc
        self._record_usage(data.get("usage") or {})
        return content

    def chat_with_tools(
        self, messages: list[dict], tools: list[dict]
    ) -> tuple[str, list[ToolCall]]:
        """原生 function calling：带 tools 请求，返回 (文本内容, 工具调用列表)。

        模型二选一：返回 content（最终回答）或返回 tool_calls（要求执行工具）。
        """
        payload = {
            "model": self._model,
            "messages": messages,
            "temperature": self._temperature,
            "tools": tools,
        }
        data = self._post(payload)
        try:
            msg = data["choices"][0]["message"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError(f"unexpected LLM response shape: {data}") from exc
        self._record_usage(data.get("usage") or {})
        content = msg.get("content") or ""
        calls = []
        for tc in msg.get("tool_calls") or []:
            try:
                args = json.loads(tc["function"].get("arguments") or "{}")
            except (json.JSONDecodeError, TypeError):
                args = {}
            calls.append(
                ToolCall(id=tc.get("id") or "", name=tc["function"]["name"], arguments=args)
            )
        return content, calls

    # ---- M6：InferGate 网关头 ----
    def gateway_headers(self, payload: dict) -> dict[str, str]:
        """构造 InferGate 请求头（M6）：会话 id + 租户 + 幂等键。

        幂等键由（会话 id, 轮次序号, 请求体哈希）确定性派生，所以"同一逻辑轮次的
        重试"永远拿到同一个键，网关据此回放已存答案；新一轮消息不同 → 新键。
        没有用户消息（纯 system 的内部调用）时不带幂等键：宁可不带，也不用随机值
        ——随机键等于放弃幂等，还会污染网关的键空间。
        """
        if not self._settings.gateway_enabled:
            return {}
        headers: dict[str, str] = {}
        tenant = (self._settings.gateway_tenant or "").strip()
        if tenant:
            headers[HEADER_TENANT] = tenant
        messages = payload.get("messages") or []
        session_id = resolve_session_id(messages, pinned=self._settings.gateway_session)
        if not session_id:
            return headers
        headers[HEADER_SESSION] = session_id
        headers[HEADER_IDEMPOTENCY_KEY] = derive_idempotency_key(
            session_id=session_id, messages=messages, payload=payload
        )
        return headers

    def _post(self, payload: dict) -> dict:
        """发送 /chat/completions，返回解析后的 JSON；网络错误/非 200 抛 LLMError。

        M6 的三条网关语义：
        * 409 infergate_idempotency_in_flight：同键的原始请求还在飞行。等
          Retry-After（缺失时用 gateway_retry_after_s）后**用同一个键**重发，
          上限 gateway_retry_attempts；到顶仍被拒就照常抛错。
        * 409 infergate_idempotency_conflict：同键配了不同请求体，说明键派生有
          bug，重试不可能成功——大声记日志后立刻失败，不做无谓循环。
        * X-InferGate-Idempotent-Replay: true：答案来自网关缓存，算正常成功，
          记日志 + 计数器 + last_gateway（"没有重复计费"的证据）。
        """
        url = f"{self._base_url}/chat/completions"
        headers = {"Authorization": f"Bearer {self._api_key}"}
        gateway_headers = self.gateway_headers(payload)
        headers.update(gateway_headers)
        key = gateway_headers.get(HEADER_IDEMPOTENCY_KEY, "")
        # 只有"带了幂等键"的请求才可能出现同键并发，也才值得为重试多等
        max_attempts = max(1, int(self._settings.gateway_retry_attempts)) if key else 1
        attempts = 1
        resp = None
        for attempt in range(1, max_attempts + 1):
            attempts = attempt
            try:
                resp = httpx.post(url, json=payload, headers=headers, timeout=self._timeout)
            except httpx.HTTPError as exc:  # 网络层错误（连接失败/超时）
                raise LLMError(f"LLM request failed: {exc}") from exc
            kind = _gateway_error_type(resp)
            if kind == TYPE_IDEMPOTENCY_CONFLICT:
                logger.error(
                    "InferGate 幂等键冲突（%s, key=%s, session=%s）：同一个键被用于不同的"
                    "请求体。这是 Idempotency-Key 派生的问题，重试不会有结果，直接失败。",
                    TYPE_IDEMPOTENCY_CONFLICT,
                    key or "-",
                    gateway_headers.get(HEADER_SESSION, "-"),
                )
                raise LLMError(f"LLM returned {resp.status_code}: {resp.text[:500]}")
            if kind == TYPE_IDEMPOTENCY_IN_FLIGHT and attempt < max_attempts:
                delay = _retry_after_s(resp, self._settings.gateway_retry_after_s)
                logger.warning(
                    "InferGate 报告同键请求仍在飞行（%s, key=%s）：%.2fs 后重试同一键"
                    "（第 %d/%d 次尝试）",
                    TYPE_IDEMPOTENCY_IN_FLIGHT,
                    key or "-",
                    delay,
                    attempt,
                    max_attempts,
                )
                if self._settings.metrics_enabled:
                    record_in_flight_retry(self._model)
                time.sleep(delay)
                continue
            break
        if resp is None:  # max_attempts >= 1，正常到不了这里
            raise LLMError("LLM request failed: no response")
        self._record_gateway(
            resp, key=key, session_id=gateway_headers.get(HEADER_SESSION, ""), attempts=attempts
        )
        if resp.status_code != 200:
            raise LLMError(f"LLM returned {resp.status_code}: {resp.text[:500]}")
        return resp.json()

    def _record_gateway(self, resp, *, key: str, session_id: str, attempts: int) -> None:
        """把网关回放信息记到 last_gateway / 日志 / 计数器。

        全部用 getattr + get 取值：裸 OpenAI 或旧网关不返回这些头时，
        这里只是记下一堆空值，绝不影响正常返回。
        """
        headers = getattr(resp, "headers", None) or {}
        replayed = str(headers.get(HEADER_REPLAY, "") or "").strip().lower() == "true"
        self.last_gateway = GatewayOutcome(
            key=key,
            session_id=session_id,
            replayed=replayed,
            replay_origin=str(headers.get(HEADER_REPLAY_ORIGIN, "") or ""),
            replay_age_ms=_header_int(headers, HEADER_REPLAY_AGE),
            replay_upstream=str(headers.get(HEADER_REPLAY_UPSTREAM, "") or ""),
            upstream_name=str(headers.get(HEADER_UPSTREAM_NAME, "") or ""),
            attempts=attempts,
        )
        if replayed:
            logger.info(
                "InferGate 幂等回放：本轮由网关回放已存答案，未重复上游调用（key=%s, "
                "origin=%s, age=%dms, upstream=%s）",
                key or "-",
                self.last_gateway.replay_origin or "-",
                self.last_gateway.replay_age_ms,
                self.last_gateway.replay_upstream or "-",
            )
            if self._settings.metrics_enabled:
                record_idempotent_replay(self._model)

    # ---- M6：能力发现 ----
    def capability(self, model: str | None = None) -> ModelCapability | None:
        """查某个模型的声明式能力；网关不提供该端点或探测失败 → None。"""
        if not self._settings.gateway_capabilities_enabled:
            return None
        return self._capabilities.model(
            self._base_url,
            model or self._model,
            api_key=self._api_key,
            timeout=min(float(self._timeout), _CAPABILITY_TIMEOUT_S),
        )

    def context_window(self, model: str | None = None) -> int | None:
        """模型的上下文窗口（token）；未知 → None，调用方自行回退。

        注意：Warden 目前没有任何按 token 估上下文的地方（历史裁剪是
        WARDEN_CONTEXT_MAX_MESSAGES，按**消息条数**），所以这个值现在是给运维/
        未来的量化入口，没有接进 Agent 循环——接进去等于新造一套裁剪策略。
        """
        cap = self.capability(model)
        if cap is None or cap.context_window <= 0:
            return None
        return cap.context_window

    def max_output_tokens(self, model: str | None = None) -> int | None:
        """模型单次输出上限（token）；未知 → None。"""
        cap = self.capability(model)
        if cap is None or cap.max_output_tokens <= 0:
            return None
        return cap.max_output_tokens

    def _record_usage(self, usage: dict) -> None:
        """把本次调用的 token 用量记入 last_usage，并按单价折算成本记入指标（M5）。"""
        self.last_usage = Usage(
            prompt_tokens=int(usage.get("prompt_tokens") or 0),
            completion_tokens=int(usage.get("completion_tokens") or 0),
        )
        if self._settings.metrics_enabled:
            cost = compute_cost(
                self._settings, self.last_usage.prompt_tokens, self.last_usage.completion_tokens
            )
            record_llm_usage(
                self._model, self.last_usage.prompt_tokens, self.last_usage.completion_tokens, cost
            )
