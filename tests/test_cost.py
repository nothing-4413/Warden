"""M5：LLM 成本统计（token 用量 + 成本折算）。"""

import httpx
from prometheus_client import REGISTRY

from app.config import Settings
from app.harness.cost import compute_cost
from app.harness.metrics import record_llm_usage
from app.llm import LLMClient


def test_compute_cost():
    s = Settings(llm_input_price_per_1m=2.0, llm_output_price_per_1m=6.0)
    assert compute_cost(s, 1_000_000, 1_000_000) == 8.0
    assert compute_cost(Settings(), 1000, 1000) == 0.0  # 默认单价 0


def test_record_llm_usage_increments_counters():
    record_llm_usage("cost_test", prompt_tokens=10, completion_tokens=5, cost=0.001)
    assert (
        REGISTRY.get_sample_value(
            "warden_tokens_total", {"model": "cost_test", "direction": "prompt"}
        )
        == 10.0
    )
    assert (
        REGISTRY.get_sample_value(
            "warden_tokens_total", {"model": "cost_test", "direction": "completion"}
        )
        == 5.0
    )
    assert REGISTRY.get_sample_value("warden_cost_dollars_total", {"model": "cost_test"}) == 0.001


def test_llm_client_captures_usage(monkeypatch):
    class FakeResp:
        status_code = 200
        text = ""

        def json(self):
            return {
                "choices": [{"message": {"content": "hi"}}],
                "usage": {"prompt_tokens": 7, "completion_tokens": 3},
            }

    monkeypatch.setattr(httpx, "post", lambda *a, **k: FakeResp())
    client = LLMClient(Settings())
    out = client.chat([{"role": "user", "content": "hi"}])
    assert out == "hi"
    assert client.last_usage.prompt_tokens == 7
    assert client.last_usage.completion_tokens == 3
