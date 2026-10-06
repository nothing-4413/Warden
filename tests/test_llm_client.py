"""LLMClient：响应解析与错误包装（httpx 被拦掉，不联网）。

覆盖 chat / chat_with_tools 两条路径的正常解析与坏形状、网络错误包装。
"""

import httpx
import pytest

from app.config import Settings
from app.llm import LLMClient, LLMError, ToolCall


class FakeResp:
    def __init__(self, payload, status_code: int = 200, text: str = "") -> None:
        self._payload = payload
        self.status_code = status_code
        self.text = text
        self.headers: dict[str, str] = {}

    def json(self):
        return self._payload


def test_chat_with_tools_parses_content_and_calls(monkeypatch) -> None:
    payload = {
        "choices": [
            {
                "message": {
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "function": {
                                "name": "calculator",
                                "arguments": '{"expression": "2+2"}',
                            },
                        },
                        # 没有 id、参数不是合法 JSON：不能把整轮带崩
                        {"function": {"name": "broken", "arguments": "not json"}},
                    ],
                }
            }
        ],
        "usage": {"prompt_tokens": 5, "completion_tokens": 2},
    }
    monkeypatch.setattr(httpx, "post", lambda *a, **k: FakeResp(payload))
    client = LLMClient(Settings())

    content, calls = client.chat_with_tools(
        [{"role": "user", "content": "算一下 2+2"}], [{"type": "function"}]
    )

    assert content == ""
    assert calls == [
        ToolCall(id="call_1", name="calculator", arguments={"expression": "2+2"}),
        ToolCall(id="", name="broken", arguments={}),
    ]
    assert client.last_usage.prompt_tokens == 5


def test_chat_with_tools_uses_content_when_no_calls(monkeypatch) -> None:
    payload = {"choices": [{"message": {"content": "不需要工具"}}]}
    monkeypatch.setattr(httpx, "post", lambda *a, **k: FakeResp(payload))

    content, calls = LLMClient(Settings()).chat_with_tools(
        [{"role": "user", "content": "你好"}], []
    )

    assert (content, calls) == ("不需要工具", [])


@pytest.mark.parametrize("method", ["chat", "chat_with_tools"])
def test_unexpected_shape_raises_llm_error(monkeypatch, method) -> None:
    monkeypatch.setattr(httpx, "post", lambda *a, **k: FakeResp({"choices": []}))
    client = LLMClient(Settings())

    with pytest.raises(LLMError, match="unexpected LLM response shape"):
        if method == "chat":
            client.chat([{"role": "user", "content": "hi"}])
        else:
            client.chat_with_tools([{"role": "user", "content": "hi"}], [])


def test_network_error_is_wrapped_as_llm_error(monkeypatch) -> None:
    def boom(*args, **kwargs):
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(httpx, "post", boom)

    with pytest.raises(LLMError, match="LLM request failed"):
        LLMClient(Settings()).chat([{"role": "user", "content": "hi"}])


def test_non_200_status_is_wrapped_with_body(monkeypatch) -> None:
    monkeypatch.setattr(httpx, "post", lambda *a, **k: FakeResp({}, status_code=500, text="boom"))

    with pytest.raises(LLMError, match="LLM returned 500"):
        LLMClient(Settings()).chat([{"role": "user", "content": "hi"}])
