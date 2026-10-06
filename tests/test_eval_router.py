"""`scripts/eval_router.py` 的自测：全程本机，不联网。

用一个 stub 上游冒充 chat / 嵌入实例，验证分流、body 透传、上游 4xx 与上游挂掉时的表现。
客户端显式 `trust_env=False`，避免 Windows 系统代理把本机请求劫持成 502。
"""

from __future__ import annotations

import http.server
import json
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import httpx
import pytest

from scripts.eval_router import make_handler


class _StubUpstream(http.server.BaseHTTPRequestHandler):
    """每个实例有自己的 `name`，响应里带上它，方便断言请求被转到了谁。"""

    name = "stub"
    fail_status: int | None = None  # 设了就对所有请求回这个状态码
    received: list[dict[str, Any]] = []
    protocol_version = "HTTP/1.1"

    def log_message(self, *args: object) -> None:  # 测试里不要刷日志
        pass

    def _respond(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _handle(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        type(self).received.append(
            {"name": self.name, "path": self.path, "method": self.command, "body": raw}
        )
        if self.fail_status:
            self._respond(self.fail_status, {"error": "upstream says slow down"})
        else:
            self._respond(200, {"served_by": self.name, "path": self.path, "echo": len(raw)})

    def do_POST(self) -> None:
        self._handle()

    def do_GET(self) -> None:
        self._handle()


@contextmanager
def _serve(handler: type[http.server.BaseHTTPRequestHandler]) -> Iterator[str]:
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.fixture
def router() -> Iterator[tuple[str, type[_StubUpstream], type[_StubUpstream]]]:
    """起两个 stub 上游 + 一个路由器，返回 (路由器地址, chat stub, embed stub)。"""

    class ChatStub(_StubUpstream):
        name = "chat"

    class EmbedStub(_StubUpstream):
        name = "embed"

    ChatStub.received = []
    EmbedStub.received = []
    with _serve(ChatStub) as chat_url, _serve(EmbedStub) as embed_url:
        with _serve(make_handler(chat_url, embed_url)) as router_url:
            yield router_url, ChatStub, EmbedStub


def _client() -> httpx.Client:
    return httpx.Client(trust_env=False, timeout=10.0)


def test_embeddings_go_to_embed_instance(router: tuple[str, type, type]) -> None:
    url, chat, embed = router
    with _client() as client:
        resp = client.post(f"{url}/v1/embeddings", json={"model": "m", "input": ["你好"]})

    assert resp.status_code == 200
    assert resp.json()["served_by"] == "embed"
    assert json.loads(embed.received[0]["body"]) == {
        "model": "m",
        "input": ["你好"],
    }  # body 原样透传
    assert chat.received == []


def test_chat_goes_to_chat_instance(router: tuple[str, type, type]) -> None:
    url, chat, embed = router
    with _client() as client:
        resp = client.post(f"{url}/v1/chat/completions", json={"model": "m", "messages": []})
        models = client.get(f"{url}/v1/models")

    assert resp.json()["served_by"] == "chat"
    assert models.json()["served_by"] == "chat"  # /v1/models 也走对话实例
    assert embed.received == []


def test_health_is_answered_locally(router: tuple[str, type, type]) -> None:
    url, chat, embed = router
    with _client() as client:
        resp = client.get(f"{url}/health")

    assert (resp.status_code, resp.text) == (200, "ok")
    assert chat.received == [] and embed.received == []  # 没有打到上游


def test_unknown_path_is_404(router: tuple[str, type, type]) -> None:
    url, _, _ = router
    with _client() as client:
        assert client.post(f"{url}/v1/images").status_code == 404


def test_upstream_error_status_is_passed_through() -> None:
    class BoomStub(_StubUpstream):
        name = "boom"
        fail_status = 429

    BoomStub.received = []
    with _serve(BoomStub) as upstream:
        with _serve(make_handler(upstream, upstream)) as router_url:
            with _client() as client:
                resp = client.post(f"{router_url}/v1/embeddings", json={"input": ["x"]})

    assert resp.status_code == 429
    assert resp.json() == {"error": "upstream says slow down"}


def test_upstream_down_returns_502_with_reason() -> None:
    # 指向一个没人监听的端口
    dead = "http://127.0.0.1:9"
    with _serve(make_handler(dead, dead)) as router_url:
        with _client() as client:
            resp = client.post(f"{router_url}/v1/embeddings", json={"input": ["x"]})

    assert resp.status_code == 502
    assert "router:" in resp.json()["error"]
