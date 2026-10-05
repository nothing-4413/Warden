"""配置层（M0）：环境变量 → Settings 的映射、留空回落、类型校验。

测试一律传 `_env_file=None`，所以本机是否存在 .env 都不影响结果。
"""

from __future__ import annotations

import os

import pytest
from pydantic import ValidationError

from app.config import Settings, get_settings


@pytest.fixture(autouse=True)
def _clear_warden_env(monkeypatch):
    """清掉 shell 里可能存在的 WARDEN_*，让每个用例从确定的默认值出发。"""
    for key in [k for k in os.environ if k.startswith("WARDEN_")]:
        monkeypatch.delenv(key, raising=False)


def test_defaults_match_documented_values():
    s = Settings(_env_file=None)
    assert s.llm_model == "qwen2.5:7b"
    assert s.llm_base_url == "http://localhost:11434/v1"
    assert s.agent_max_steps == 10
    assert s.api_port == 8000
    assert s.retry_backoff_s == 1.0
    assert s.metrics_enabled is True
    assert s.reflect_enabled is False
    assert s.llm_input_price_per_1m == 0.0 and s.llm_output_price_per_1m == 0.0
    assert s.mcp_servers == []
    assert s.rss_source_list == [
        "https://news.ycombinator.com/rss",
        "https://export.arxiv.org/rss/cs.AI",
    ]


def test_env_vars_map_by_prefix_and_coerce_types(monkeypatch):
    # 单价字段名里的 1m 必须和 WARDEN_..._PER_1M 对上（历史上这里是 _PER_MTOK）
    monkeypatch.setenv("WARDEN_LLM_INPUT_PRICE_PER_1M", "2")
    monkeypatch.setenv("WARDEN_LLM_OUTPUT_PRICE_PER_1M", "6")
    monkeypatch.setenv("WARDEN_AGENT_MAX_STEPS", "3")
    monkeypatch.setenv("WARDEN_REFLECT_ENABLED", "true")
    monkeypatch.setenv("WARDEN_LLM_TEMPERATURE", "0.7")

    s = Settings(_env_file=None)
    assert s.llm_input_price_per_1m == 2.0
    assert s.llm_output_price_per_1m == 6.0
    assert s.agent_max_steps == 3
    assert s.reflect_enabled is True
    assert s.llm_temperature == 0.7


def test_mcp_servers_parsed_from_json(monkeypatch):
    monkeypatch.setenv(
        "WARDEN_MCP_SERVERS", '[{"name": "fs", "command": ["npx", "-y", "server-filesystem"]}]'
    )
    s = Settings(_env_file=None)
    assert s.mcp_servers == [
        {"name": "fs", "command": ["npx", "-y", "server-filesystem"]},
    ]


def test_empty_values_fall_back_to_defaults(monkeypatch):
    """.env 里 `KEY=`（留空）不能把 JSON / 数值字段炸掉，应回落默认值。"""
    monkeypatch.setenv("WARDEN_MCP_SERVERS", "")
    monkeypatch.setenv("WARDEN_NOTES_DIR", "")
    monkeypatch.setenv("WARDEN_GATEWAY_SESSION", "")

    s = Settings(_env_file=None)
    assert s.mcp_servers == []
    assert s.notes_dir == ""
    assert s.gateway_session == ""


def test_bad_type_reports_the_field_name(monkeypatch):
    monkeypatch.setenv("WARDEN_AGENT_MAX_STEPS", "很多步")
    with pytest.raises(ValidationError) as exc:
        Settings(_env_file=None)
    assert "agent_max_steps" in str(exc.value)


def test_settings_are_frozen():
    s = Settings(_env_file=None)
    with pytest.raises(ValidationError):
        s.llm_model = "别的模型"  # type: ignore[misc]


def test_get_settings_is_a_singleton():
    assert get_settings() is get_settings()
