"""出站 HTTP 的代理处理（app/net.py）：本机目标必须绕开系统代理。

背景（实测）：Windows 上 httpx 默认 trust_env=True 会读注册表代理，本机 Clash 类代理
对 127.0.0.1 目标也返回 502 空 body，而被代理的 llama-server / Ollama 侧看不到请求。
"""

from __future__ import annotations

import httpx
import pytest

from app.config import Settings
from app.gateway.capabilities import CapabilityCache
from app.llm import LLMClient
from app.memory.embeddings import EmbeddingClient, EmbeddingError
from app.net import httpx_env_kwargs, is_loopback_url

CHAT_OK = {"choices": [{"message": {"content": "ok"}}], "usage": {}}


class FakeResp:
    """最小 httpx.Response 替身（只实现被用到的部分）。"""

    def __init__(self, status_code=200, payload=None, headers=None, text=""):
        self.status_code = status_code
        self._payload = payload
        self.headers = headers or {}
        self.text = text

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("boom", request=None, response=None)


class Recorder:
    """替身 httpx 调用：记下 kwargs，回放固定响应（或抛异常）。"""

    def __init__(self, response):
        self.response = response
        self.calls = []

    def __call__(self, url, **kwargs):
        self.calls.append({"url": url, **kwargs})
        if isinstance(self.response, Exception):
            raise self.response
        return self.response

    @property
    def trust_env(self):
        return self.calls[0].get("trust_env", "缺省(读环境)")


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:11434/v1",
        "http://localhost:11434/v1",
        "http://LOCALHOST:8000/",
        "http://127.0.0.2:1234/v1",
        "http://[::1]:11434/v1",
    ],
)
def test_is_loopback_url_true(url):
    assert is_loopback_url(url) is True


@pytest.mark.parametrize(
    "url",
    [
        "https://api.openai.com/v1",
        "http://192.168.1.10:11434/v1",
        "http://ollama.internal:11434/v1",
        "",
        "not a url",
    ],
)
def test_is_loopback_url_false(url):
    assert is_loopback_url(url) is False


def test_httpx_env_kwargs_only_for_loopback():
    assert httpx_env_kwargs("http://127.0.0.1:11434/v1") == {"trust_env": False}
    assert httpx_env_kwargs("https://api.openai.com/v1") == {}


def test_llm_client_disables_env_proxy_for_local_base_url(monkeypatch):
    rec = Recorder(FakeResp(payload=CHAT_OK))
    monkeypatch.setattr(httpx, "post", rec)
    settings = Settings(llm_base_url="http://127.0.0.1:11434/v1", llm_model="m")
    assert LLMClient(settings).chat([{"role": "user", "content": "hi"}]) == "ok"
    assert rec.calls[0]["url"] == "http://127.0.0.1:11434/v1/chat/completions"
    assert rec.trust_env is False


def test_llm_client_keeps_env_proxy_for_remote_base_url(monkeypatch):
    rec = Recorder(FakeResp(payload=CHAT_OK))
    monkeypatch.setattr(httpx, "post", rec)
    settings = Settings(llm_base_url="https://api.openai.com/v1", llm_model="gpt-4o-mini")
    LLMClient(settings).chat([{"role": "user", "content": "hi"}])
    # 外网目标保持 httpx 默认：企业代理 / SSL_CERT_FILE 仍生效
    assert "trust_env" not in rec.calls[0]


def test_embedding_client_disables_env_proxy_and_orders_by_index(monkeypatch):
    payload = {
        "data": [
            {"index": 1, "embedding": [0.2]},
            {"index": 0, "embedding": [0.1]},
        ]
    }
    rec = Recorder(FakeResp(payload=payload))
    monkeypatch.setattr(httpx, "post", rec)
    settings = Settings(llm_base_url="http://localhost:11434/v1", embedding_model="emb")
    out = EmbeddingClient(settings).embed(["a", "b"])
    assert out == [[0.1], [0.2]]  # 后端乱序返回也要按 index 纠正
    assert rec.calls[0]["url"] == "http://localhost:11434/v1/embeddings"
    assert rec.trust_env is False


def test_embedding_client_wraps_transport_error(monkeypatch):
    monkeypatch.setattr(httpx, "post", Recorder(httpx.ConnectError("boom")))
    settings = Settings(llm_base_url="http://127.0.0.1:11434/v1")
    with pytest.raises(EmbeddingError):
        EmbeddingClient(settings).embed(["a"])


def test_embedding_client_wraps_bad_shape(monkeypatch):
    monkeypatch.setattr(httpx, "post", Recorder(FakeResp(payload={"data": [{"no_index": 1}]})))
    settings = Settings(llm_base_url="http://127.0.0.1:11434/v1")
    with pytest.raises(EmbeddingError):
        EmbeddingClient(settings).embed(["a"])


def test_capability_probe_disables_env_proxy_for_local_gateway(monkeypatch):
    report = {"models": [{"model": "mock-gpt", "context_window": 128000}]}
    rec = Recorder(FakeResp(payload=report))
    monkeypatch.setattr(httpx, "get", rec)
    cache = CapabilityCache(ttl_s=60)
    assert cache.context_window("http://127.0.0.1:19000/v1", "mock-gpt") == 128000
    assert rec.calls[0]["url"] == "http://127.0.0.1:19000/v1/capabilities"
    assert rec.trust_env is False
