"""M6：InferGate 网关集成（会话/租户/幂等键 + 能力发现）。

全部用 monkeypatch 拦住 httpx，不依赖真实网关；对真实网关的端到端验证见
infergate 仓库的 tmp/warden_m6_e2e.py，以及 tests/test_gateway_live.py
（未设 WARDEN_E2E_GATEWAY 时自动 skip）。
"""
from __future__ import annotations

import httpx
import pytest
from prometheus_client import REGISTRY

from app.config import Settings
from app.gateway import (
    HEADER_IDEMPOTENCY_KEY,
    HEADER_REPLAY,
    HEADER_SESSION,
    HEADER_TENANT,
    CapabilityCache,
    derive_idempotency_key,
    derive_session_id,
    gateway_session_scope,
)
from app.gateway.capabilities import capabilities_url
from app.llm import LLMClient, LLMError

CHAT_OK = {"choices": [{"message": {"content": "ok"}}],
           "usage": {"prompt_tokens": 1, "completion_tokens": 1}}


class FakeResp:
    """最小 httpx.Response 替身；headers 缺省是空 dict（取值不会 AttributeError）。"""

    def __init__(self, status_code=200, payload=None, headers=None, text=""):
        self.status_code = status_code
        self._payload = CHAT_OK if payload is None else payload
        self.headers = headers or {}
        self.text = text

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


class Recorder:
    """按顺序回放 responses（用完就重复最后一个），并记下每次请求。"""

    def __init__(self, responses, kind="post"):
        self.responses = responses
        self.kind = kind
        self.calls = []

    def __call__(self, url, **kwargs):
        self.calls.append({"url": url, **kwargs})
        idx = min(len(self.calls) - 1, len(self.responses) - 1)
        return self.responses[idx]

    @property
    def urls(self):
        return [c["url"] for c in self.calls]

    @property
    def headers(self):
        return [dict(c.get("headers") or {}) for c in self.calls]


def in_flight_body():
    """InferGate 的真实形状：OpenAI 风格嵌套 error.type（internal/gateway/errors.go）。"""
    return {"error": {"message": "in flight", "type": "infergate_idempotency_in_flight"}}


# ---- A. 三个请求头 ----

def test_three_headers_sent_with_stable_values(monkeypatch):
    rec = Recorder([FakeResp()])
    monkeypatch.setattr(httpx, "post", rec)
    client = LLMClient(Settings(gateway_tenant="team-a"))
    turn1 = [{"role": "user", "content": "第一轮：帮我看下日志"}]
    client.chat(turn1)
    turn2 = [*turn1, {"role": "assistant", "content": "ok"},
             {"role": "user", "content": "第二轮：再看下磁盘"}]
    client.chat(turn2)

    h1, h2 = rec.headers
    assert h1[HEADER_TENANT] == h2[HEADER_TENANT] == "team-a"
    # 同一会话：两轮必须是同一个 session（账本按它累计这次对话）
    assert h1[HEADER_SESSION] == h2[HEADER_SESSION] == derive_session_id(turn1)
    assert h1[HEADER_SESSION].startswith("warden-")
    # 不同轮次：幂等键必须不同，否则第二轮会被网关当成第一轮的回放
    assert h1[HEADER_IDEMPOTENCY_KEY] != h2[HEADER_IDEMPOTENCY_KEY]


def test_idempotency_key_reproducible_across_processes(monkeypatch):
    """同一逻辑轮次跨进程重启（新建 client）必须派生出同一个键。"""
    messages = [{"role": "user", "content": "同一个问题"}]
    keys = []
    for _ in range(2):
        rec = Recorder([FakeResp()])
        monkeypatch.setattr(httpx, "post", rec)
        LLMClient(Settings()).chat(messages)
        keys.append(rec.headers[0][HEADER_IDEMPOTENCY_KEY])
    assert keys[0] == keys[1]


def test_idempotency_key_ignores_dict_ordering():
    """键只取决于内容：字段顺序变了也不该换键（canonical JSON）。"""
    msgs = [{"role": "user", "content": "q"}]
    a = derive_idempotency_key(session_id="s", messages=msgs, payload={"a": 1, "b": 2})
    b = derive_idempotency_key(session_id="s", messages=msgs, payload={"b": 2, "a": 1})
    assert a == b
    # 内容变了就必须换键，否则网关会拿旧答案回放
    c = derive_idempotency_key(session_id="s", messages=msgs, payload={"a": 1, "b": 3})
    assert a != c


def test_system_only_call_has_no_idempotency_key(monkeypatch):
    """没有用户消息时不编随机幂等键（随机键等于放弃幂等）。"""
    rec = Recorder([FakeResp()])
    monkeypatch.setattr(httpx, "post", rec)
    LLMClient(Settings(gateway_tenant="t")).chat([{"role": "system", "content": "summarize"}])
    h = rec.headers[0]
    assert HEADER_IDEMPOTENCY_KEY not in h
    assert HEADER_SESSION not in h
    assert h[HEADER_TENANT] == "t"


def test_gateway_headers_can_be_disabled(monkeypatch):
    rec = Recorder([FakeResp()])
    monkeypatch.setattr(httpx, "post", rec)
    LLMClient(Settings(gateway_enabled=False)).chat([{"role": "user", "content": "q"}])
    assert set(rec.headers[0]) == {"Authorization"}


def test_scope_keeps_session_stable_when_history_is_trimmed(monkeypatch):
    """窗口裁剪（甚至开头被换成摘要）后，会话 id 仍由完整历史决定。"""
    full = [{"role": "user", "content": "会话开头"}] + [
        {"role": "assistant", "content": str(i)} for i in range(40)]
    rec = Recorder([FakeResp()])
    monkeypatch.setattr(httpx, "post", rec)
    client = LLMClient(Settings())
    with gateway_session_scope(derive_session_id(full)):
        client.chat([{"role": "user", "content": "[Earlier conversation summary]\n..."},
                     {"role": "user", "content": "继续"}])
    assert rec.headers[0][HEADER_SESSION] == derive_session_id(full)


def test_pinned_session_wins_over_derivation(monkeypatch):
    rec = Recorder([FakeResp()])
    monkeypatch.setattr(httpx, "post", rec)
    LLMClient(Settings(gateway_session="pinned-session")).chat(
        [{"role": "user", "content": "q"}])
    assert rec.headers[0][HEADER_SESSION] == "pinned-session"


# ---- A2. 409 in_flight 可重试 / 409 conflict 快速失败 ----

def test_in_flight_retried_once_then_succeeds(monkeypatch):
    rec = Recorder([FakeResp(409, in_flight_body(), {"Retry-After": "1"}), FakeResp()])
    sleeps: list[float] = []
    monkeypatch.setattr(httpx, "post", rec)
    monkeypatch.setattr("time.sleep", lambda s: sleeps.append(s))
    client = LLMClient(Settings(gateway_retry_attempts=3))

    assert client.chat([{"role": "user", "content": "q"}]) == "ok"
    assert len(rec.calls) == 2
    # 重试用的是同一个幂等键 —— 这正是网关能回放而不是重新干活的前提
    assert rec.headers[0][HEADER_IDEMPOTENCY_KEY] == rec.headers[1][HEADER_IDEMPOTENCY_KEY]
    assert sleeps == [1.0]                       # 尊重响应里的 Retry-After
    assert client.last_gateway.attempts == 2


def test_in_flight_uses_default_delay_without_retry_after(monkeypatch):
    rec = Recorder([FakeResp(409, in_flight_body()), FakeResp()])
    sleeps: list[float] = []
    monkeypatch.setattr(httpx, "post", rec)
    monkeypatch.setattr("time.sleep", lambda s: sleeps.append(s))
    LLMClient(Settings(gateway_retry_after_s=0.25)).chat([{"role": "user", "content": "q"}])
    assert sleeps == [0.25]


def test_in_flight_gives_up_after_max_attempts(monkeypatch):
    rec = Recorder([FakeResp(409, in_flight_body(), {"Retry-After": "0"})])
    monkeypatch.setattr(httpx, "post", rec)
    monkeypatch.setattr("time.sleep", lambda s: None)
    client = LLMClient(Settings(gateway_retry_attempts=3))
    with pytest.raises(LLMError) as excinfo:
        client.chat([{"role": "user", "content": "q"}])
    assert len(rec.calls) == 3
    assert "409" in str(excinfo.value)


def test_conflict_fails_fast(monkeypatch):
    rec = Recorder([FakeResp(409, {"error": {"message": "conflict",
                                             "type": "infergate_idempotency_conflict"}}),
                    FakeResp()])
    sleeps: list[float] = []
    monkeypatch.setattr(httpx, "post", rec)
    monkeypatch.setattr("time.sleep", lambda s: sleeps.append(s))
    with pytest.raises(LLMError):
        LLMClient(Settings()).chat([{"role": "user", "content": "q"}])
    assert len(rec.calls) == 1     # 键派生错了，重试再多次也是同一个错
    assert sleeps == []


def test_top_level_error_type_also_recognised(monkeypatch):
    """错误体写成顶层 {"type": ...} 时也要认出来（两种形状都出现过）。"""
    rec = Recorder([FakeResp(409, {"type": "infergate_idempotency_conflict"}), FakeResp()])
    monkeypatch.setattr(httpx, "post", rec)
    with pytest.raises(LLMError):
        LLMClient(Settings()).chat([{"role": "user", "content": "q"}])
    assert len(rec.calls) == 1


def test_garbage_conflict_body_does_not_crash(monkeypatch):
    """409 但响应体不是 JSON：按普通 HTTP 错误处理，不许在解析里炸掉。"""
    rec = Recorder([FakeResp(409, ValueError("not json"), text="<html>409</html>")])
    monkeypatch.setattr(httpx, "post", rec)
    with pytest.raises(LLMError):
        LLMClient(Settings()).chat([{"role": "user", "content": "q"}])
    assert len(rec.calls) == 1


# ---- A3. 幂等回放 = 正常成功 ----

def test_replay_is_a_normal_success(monkeypatch):
    rec = Recorder([FakeResp(200, CHAT_OK, {
        HEADER_REPLAY: "true",
        "X-InferGate-Idempotent-Origin": "req-original-1",
        "X-InferGate-Idempotent-Age": "1234",
        "X-InferGate-Idempotent-Upstream": "scripted-mock",
        "X-InferGate-Upstream-Name": "replay",
    })])
    monkeypatch.setattr(httpx, "post", rec)
    before = REGISTRY.get_sample_value(
        "warden_gateway_idempotent_replays_total", {"model": "gw-replay"}) or 0.0

    client = LLMClient(Settings(llm_model="gw-replay"))
    assert client.chat([{"role": "user", "content": "q"}]) == "ok"

    gw = client.last_gateway
    assert gw.replayed is True
    assert gw.replay_origin == "req-original-1"
    assert gw.replay_age_ms == 1234
    assert gw.replay_upstream == "scripted-mock"
    assert gw.upstream_name == "replay"
    assert client.last_usage.prompt_tokens == 1     # 回放照样带 usage，照常记账
    after = REGISTRY.get_sample_value(
        "warden_gateway_idempotent_replays_total", {"model": "gw-replay"})
    assert after == before + 1


def test_non_replay_response_is_recorded_as_false(monkeypatch):
    """真正干活时 InferGate 也会带 Replay: false。"""
    rec = Recorder([FakeResp(200, CHAT_OK, {HEADER_REPLAY: "false",
                                            "X-InferGate-Upstream-Name": "scripted-mock"})])
    monkeypatch.setattr(httpx, "post", rec)
    client = LLMClient(Settings())
    client.chat([{"role": "user", "content": "q"}])
    assert client.last_gateway.replayed is False
    assert client.last_gateway.upstream_name == "scripted-mock"


# ---- A4. 容忍没有任何 InferGate 头的端点 ----

def test_bare_gateway_without_any_infergate_headers(monkeypatch):
    class BareResp:
        status_code = 200
        text = ""

        def json(self):
            return CHAT_OK

    monkeypatch.setattr(httpx, "post", lambda *a, **k: BareResp())
    client = LLMClient(Settings())
    assert client.chat([{"role": "user", "content": "q"}]) == "ok"
    assert client.last_gateway is not None
    assert client.last_gateway.replayed is False


# ---- B. 能力发现 ----

CAPS = {
    "generated_at": "2024-01-01T00:00:00Z",
    "capabilities": ["chat", "tools"],
    "models": [
        {"model": "mock-gpt", "capabilities": ["chat", "tools"],
         "context_window": 128000, "max_output_tokens": 4096, "available": True},
        # omitempty：值为 0 的字段在真实响应里直接不出现
        {"model": "tiny", "capabilities": ["chat"], "available": True},
    ],
    "upstreams": [{"name": "scripted-mock", "state": "closed"}],
}


def test_capabilities_url():
    assert capabilities_url("http://127.0.0.1:18939/v1") == \
        "http://127.0.0.1:18939/v1/capabilities"
    assert capabilities_url("http://127.0.0.1:18939/") == \
        "http://127.0.0.1:18939/v1/capabilities"
    assert capabilities_url("http://gw.openai.com/v1/") == \
        "http://gw.openai.com/v1/capabilities"


def test_capability_lookup_parses_context_window(monkeypatch):
    rec = Recorder([FakeResp(200, CAPS)])
    monkeypatch.setattr(httpx, "get", rec)
    client = LLMClient(Settings(llm_base_url="http://gw:1/v1", llm_model="mock-gpt"),
                       capabilities=CapabilityCache(ttl_s=60))

    assert client.context_window() == 128000
    assert client.max_output_tokens() == 4096
    assert client.capability().capabilities == ("chat", "tools")
    assert client.context_window("tiny") is None    # 字段缺省 = 未知，不是 0
    assert client.context_window("nope") is None
    assert rec.urls == ["http://gw:1/v1/capabilities"]   # TTL 内只探测一次


def test_capabilities_500_degrades(monkeypatch):
    monkeypatch.setattr(httpx, "get", Recorder([FakeResp(500, {"error": "boom"}, text="boom")]))
    client = LLMClient(Settings(), capabilities=CapabilityCache(ttl_s=60))
    assert client.context_window() is None
    assert client.max_output_tokens() is None


def test_capabilities_network_error_degrades(monkeypatch):
    def boom(*args, **kwargs):
        raise httpx.ConnectError("no route to host")

    monkeypatch.setattr(httpx, "get", boom)
    client = LLMClient(Settings(), capabilities=CapabilityCache(ttl_s=60))
    assert client.context_window() is None


def test_capabilities_non_json_degrades(monkeypatch):
    monkeypatch.setattr(httpx, "get",
                        Recorder([FakeResp(200, ValueError("not json"), text="<html>")]))
    client = LLMClient(Settings(), capabilities=CapabilityCache(ttl_s=60))
    assert client.context_window() is None


def test_capabilities_failure_is_cached(monkeypatch):
    """失败也进缓存：否则对着不提供该端点的网关，每次 LLM 调用都要多打一次探测。"""
    rec = Recorder([FakeResp(500, {}, text="")])
    monkeypatch.setattr(httpx, "get", rec)
    cache = CapabilityCache(ttl_s=60)
    assert cache.context_window("http://gw:1/v1", "m") is None
    assert cache.context_window("http://gw:1/v1", "m") is None
    assert len(rec.calls) == 1


def test_capabilities_refetch_after_ttl(monkeypatch):
    rec = Recorder([FakeResp(200, CAPS)])
    monkeypatch.setattr(httpx, "get", rec)
    cache = CapabilityCache(ttl_s=0)      # 0 = 不缓存
    assert cache.context_window("http://gw:1/v1", "mock-gpt") == 128000
    assert cache.context_window("http://gw:1/v1", "mock-gpt") == 128000
    assert len(rec.calls) == 2


def test_capabilities_can_be_disabled(monkeypatch):
    rec = Recorder([FakeResp(200, CAPS)])
    monkeypatch.setattr(httpx, "get", rec)
    client = LLMClient(Settings(gateway_capabilities_enabled=False),
                       capabilities=CapabilityCache(ttl_s=60))
    assert client.context_window() is None
    assert rec.calls == []
