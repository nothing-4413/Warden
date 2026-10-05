"""M6 实网回归：打真实 InferGate 的用例，默认跳过。

只有显式给出网关地址才会跑——默认的 pytest 不碰网络，也不需要网关存在：

    $env:WARDEN_GATEWAY_E2E_URL = "http://127.0.0.1:18939"
    $env:WARDEN_GATEWAY_E2E_MOCK = "http://127.0.0.1:19930"   # 只有重放计数用例需要
    .\\.venv\\Scripts\\python.exe -m pytest tests\\test_gateway_live.py -q

断言的是线路上的事实，不是代码自述：两轮对话在账本里是同一个会话；重放同一轮时
mock 上游的 /calls 计数不变、响应带 X-InferGate-Idempotent-Replay: true。
"""

import os
import uuid
from dataclasses import replace

import httpx
import pytest

from app.config import get_settings
from app.gateway import derive_session_id
from app.llm import LLMClient

GATEWAY = os.getenv("WARDEN_GATEWAY_E2E_URL", "").rstrip("/")
MOCK = os.getenv("WARDEN_GATEWAY_E2E_MOCK", "").rstrip("/")
TENANT = os.getenv("WARDEN_GATEWAY_E2E_TENANT", "warden-e2e-pytest")
MODEL = os.getenv("WARDEN_GATEWAY_E2E_MODEL", "mock-gpt")

live = pytest.mark.skipif(not GATEWAY, reason="需要真实网关：设置 WARDEN_GATEWAY_E2E_URL")
needs_mock = pytest.mark.skipif(
    not (GATEWAY and MOCK), reason="需要真实网关与 mock 上游：设置两个 E2E 环境变量"
)


@pytest.fixture(autouse=True)
def _no_proxy_for_loopback(monkeypatch):
    """本机回环直连，不走系统代理（有些机器注册表里配着 HTTP 代理）。

    httpx 每次调用都新建 client 并重读环境，所以在这里 setenv 就够用；
    生产代码不动——远程 LLM 走代理本来就是对的。
    """
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")


def _client() -> LLMClient:
    settings = replace(
        get_settings(),
        llm_base_url=f"{GATEWAY}/v1",
        llm_api_key="mock-key",
        llm_model=MODEL,
        gateway_tenant=TENANT,
        gateway_session="",
    )
    return LLMClient(settings)


def _turn_pair(nonce: str) -> tuple[list[dict], list[dict]]:
    """一组两轮对话。首条用户消息带随机 nonce，所以每次运行的会话互不干扰。"""
    turn1 = [{"role": "user", "content": f"live-e2e {nonce} 第一轮"}]
    turn2 = turn1 + [
        {"role": "assistant", "content": "（占位回复）"},
        {"role": "user", "content": f"live-e2e {nonce} 第二轮"},
    ]
    return turn1, turn2


def _calls() -> int:
    return int(httpx.get(f"{MOCK}/calls", timeout=5.0).json()["calls"])


@live
def test_live_two_turns_are_one_session_in_the_ledger():
    client = _client()
    turn1, turn2 = _turn_pair(uuid.uuid4().hex)

    assert client.chat(turn1).strip()
    session_id = client.last_gateway.session_id
    assert session_id == derive_session_id(turn1)

    assert client.chat(turn2).strip()
    assert client.last_gateway.session_id == session_id, "两轮必须是同一条会话"
    assert client.last_gateway.replayed is False
    assert client.last_gateway.key != "", "网关请求必须带幂等键"

    resp = httpx.get(
        f"{GATEWAY}/admin/sessions/{session_id}", params={"tenant": TENANT}, timeout=10.0
    )
    assert resp.status_code == 200, resp.text
    sess = resp.json()
    assert sess["tenant"] == TENANT
    assert sess["requests"] == 2 and sess["ok"] == 2 and sess["failed"] == 0
    assert MODEL in sess["models"]
    assert sess["prompt_tokens"] > 0 and sess["cost_usd"] > 0


@needs_mock
def test_live_replay_is_served_by_the_gateway_not_the_upstream():
    client = _client()
    turn1, turn2 = _turn_pair(uuid.uuid4().hex)

    client.chat(turn1)
    first = client.chat(turn2)
    key = client.last_gateway.key
    calls_after_real_work = _calls()

    # 同一逻辑轮次重发：同一个键 → 网关回放，上游计数不能动
    again = client.chat(turn2)
    assert again == first
    assert client.last_gateway.replayed is True
    assert client.last_gateway.key == key
    assert client.last_gateway.replay_upstream != ""
    assert client.last_gateway.upstream_name == "replay"
    assert _calls() == calls_after_real_work, "回放不该再打上游"

    sess = httpx.get(
        f"{GATEWAY}/admin/sessions/{client.last_gateway.session_id}",
        params={"tenant": TENANT},
        timeout=10.0,
    ).json()
    assert sess["requests"] == 3
    assert sess["idempotent_replays"] == 1


@live
def test_live_capabilities_report_the_context_window():
    client = _client()
    assert client.context_window() and client.max_output_tokens()
