"""InferGate 的会话身份与幂等键派生（M6）。

Warden 原本没有"会话"这个概念：唯一稳定身份是 trace_id（= run id），而它是每次
run 现生成的 uuid4，天生"每次都不一样"，拿它当会话 id 只会把一次对话拆成 N 个
会话，账本（GET /admin/sessions）也就统计不出"这次对话花了多少"。

所以这里的规则全部是**确定性派生**，不看时钟、不用随机数，只吃请求内容：

* 会话 id（X-InferGate-Session）：由一次会话的**首条用户消息**摘要而成。
  每轮请求都会带上完整历史，首条用户消息在整个会话里不变 → 同一会话的每一轮
  都是同一个 id；换一个会话（开头那句话不同）自然就是另一个 id。
* 幂等键（Idempotency-Key）：由（会话 id, 轮次序号, 请求体哈希）摘要而成。
  同一逻辑轮次的失败重试重放的是同一份请求体、同一个序号 → 同一个键，网关回放
  已存答案而不是再花一次上游调用（进程重启后重试也一样命中）。
  真正的新一轮消息更多、内容不同 → 序号与哈希都变 → 新键。

两者都只用 sha256，所以跨进程重启可复现；长度截断到 32/40 hex，
避免把超长头塞给网关。
"""
from __future__ import annotations

import contextlib
import contextvars
import hashlib
import json
from typing import Any, Iterable

# 与 InferGate internal/gateway/headers.go 中的拼写保持一致
HEADER_SESSION = "X-InferGate-Session"
HEADER_TENANT = "X-InferGate-Tenant"
HEADER_IDEMPOTENCY_KEY = "Idempotency-Key"  # OpenAI / Stripe SDK 的既有拼写
HEADER_REPLAY = "X-InferGate-Idempotent-Replay"
HEADER_REPLAY_ORIGIN = "X-InferGate-Idempotent-Origin"
HEADER_REPLAY_AGE = "X-InferGate-Idempotent-Age"
HEADER_REPLAY_UPSTREAM = "X-InferGate-Idempotent-Upstream"
HEADER_UPSTREAM_NAME = "X-InferGate-Upstream-Name"

# 生成的 id 统一带前缀，方便在网关日志/账本里一眼认出是 Warden 发来的
_ID_PREFIX = "warden-"
_FIELD_SEP = "\x1f"  # 不会出现在消息正文里的分隔符，防止拼接歧义


def _digest(parts: Iterable[str], length: int) -> str:
    """把若干段字符串按无歧义方式拼起来做 sha256，取前 length 个 hex 字符。"""
    h = hashlib.sha256()
    for part in parts:
        h.update(part.encode("utf-8"))
        h.update(_FIELD_SEP.encode("utf-8"))
    return h.hexdigest()[:length]


def _first_user_content(messages: Any) -> str:
    """取消息列表里第一条有内容的 user 消息正文（没有则空串）。"""
    for msg in messages or []:
        if not isinstance(msg, dict):
            continue
        if msg.get("role") != "user":
            continue
        content = msg.get("content")
        if isinstance(content, str) and content.strip():
            return content.strip()
    return ""


def derive_session_id(messages: Any) -> str | None:
    """由会话首条用户消息派生会话 id；取不到（无用户消息）时返回 None。

    调用方若已把上下文窗口裁剪过（BaseAgent._history_with_summary 会丢掉最旧
    消息、甚至把开头换成摘要），应传**裁剪前**的完整历史，否则会话 id 会随窗口
    滑动而漂移。
    """
    opener = _first_user_content(messages)
    if not opener:
        return None
    return _ID_PREFIX + _digest([opener], 32)


def canonical_body(payload: Any) -> str:
    """请求体的确定性序列化（键排序、无多余空白），用于算哈希。"""
    return json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def derive_idempotency_key(*, session_id: str, messages: Any, payload: Any) -> str:
    """由（会话 id, 轮次序号, 请求体哈希）派生幂等键。

    轮次序号用历史消息条数近似：一轮对话里每次追加消息都会让它变大，所以新一轮
    必然是新键；而"同一轮的失败重试"重放的还是同一份 messages + payload，
    序号和哈希都不变 → 同键 → 网关回放（X-InferGate-Idempotent-Replay: true），
    这是"没有被重复计费"的证据。
    """
    turn_index = len(messages or [])
    return _ID_PREFIX + _digest(
        [session_id, str(turn_index), canonical_body(payload)], 40
    )


# 会话 id 的隐式作用域：BaseAgent 在整轮 run() 里按完整历史设置一次，
# 于是这一轮里的每一次 LLM 调用（含摘要压缩、自评）都归属同一个会话。
_session_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "warden_gateway_session", default=None
)


def current_session_id() -> str | None:
    """当前作用域里生效的会话 id（没设置则为 None）。"""
    return _session_var.get()


@contextlib.contextmanager
def gateway_session_scope(session_id: str | None):
    """在 with 块内把会话 id 固定下来，退出时恢复原值（可嵌套）。"""
    token = _session_var.set(session_id or None)
    try:
        yield session_id
    finally:
        _session_var.reset(token)


def resolve_session_id(messages: Any, *, pinned: str = "") -> str | None:
    """会话 id 的取值优先级：配置显式钉死 > 当前作用域 > 由 messages 现推。

    pinned（WARDEN_GATEWAY_SESSION）留给需要把流量固定到一个会话的场景
    （压测、手工对账、端到端验证）；日常让它留空走派生规则。
    """
    pinned = (pinned or "").strip()
    if pinned:
        return pinned
    scoped = current_session_id()
    if scoped:
        return scoped
    return derive_session_id(messages)
