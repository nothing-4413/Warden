import pytest

from app.tools import build_default_registry


def test_default_registry_has_two_tools():
    reg = build_default_registry()
    assert set(reg.names()) == {"calculator", "get_current_time"}


def test_calculator_executes():
    reg = build_default_registry()
    assert reg.get("calculator").run({"expression": "12 * 7 + 3"}) == 87


def test_calculator_rejects_unsafe():
    reg = build_default_registry()
    with pytest.raises(ValueError):
        reg.get("calculator").run({"expression": "__import__('os').system('ls')"})


def test_datetime_returns_iso_string():
    reg = build_default_registry()
    out = reg.get("get_current_time").run({})
    assert isinstance(out, str)
    assert "T" in out  # ISO 8601 分隔符
