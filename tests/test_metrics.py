"""Prometheus 指标：record_run 计数 + /metrics 文本输出。"""
from prometheus_client import REGISTRY

from app.harness.metrics import record_run, metrics_text

_LABELS = {"kind": "chat", "name": "metric_probe", "status": "ok"}


def test_record_run_increments_counter():
    before = REGISTRY.get_sample_value("warden_runs_total", _LABELS) or 0.0
    record_run("chat", "metric_probe", "ok", 1.5)
    after = REGISTRY.get_sample_value("warden_runs_total", _LABELS)
    assert after == before + 1.0


def test_metrics_text_contains_warden_metrics():
    text = metrics_text()
    assert "warden_runs_total" in text
    assert "warden_run_duration_seconds" in text
