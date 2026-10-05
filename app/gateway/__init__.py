"""InferGate 网关集成（M6）：会话/租户/幂等键 + 能力发现。

Warden 与 InferGate 之间只差三个请求头和一个 GET：这里集中生成它们，
LLM 客户端（app/llm.py）是唯一使用者 —— 不新增第二条 HTTP 通路。

网关不认识这些头时（裸 OpenAI / 旧版 InferGate / Ollama）一切都照旧：
未知请求头会被忽略，能力探测失败返回 None，调用方退化为原有行为。
"""

from .capabilities import CapabilityCache, ModelCapability, get_capability_cache
from .identity import (
    HEADER_IDEMPOTENCY_KEY,
    HEADER_REPLAY,
    HEADER_REPLAY_AGE,
    HEADER_REPLAY_ORIGIN,
    HEADER_REPLAY_UPSTREAM,
    HEADER_SESSION,
    HEADER_TENANT,
    HEADER_UPSTREAM_NAME,
    current_session_id,
    derive_idempotency_key,
    derive_session_id,
    gateway_session_scope,
    resolve_session_id,
)

__all__ = [
    "HEADER_IDEMPOTENCY_KEY",
    "HEADER_REPLAY",
    "HEADER_REPLAY_AGE",
    "HEADER_REPLAY_ORIGIN",
    "HEADER_REPLAY_UPSTREAM",
    "HEADER_SESSION",
    "HEADER_TENANT",
    "HEADER_UPSTREAM_NAME",
    "CapabilityCache",
    "ModelCapability",
    "current_session_id",
    "derive_idempotency_key",
    "derive_session_id",
    "gateway_session_scope",
    "get_capability_cache",
    "resolve_session_id",
]
