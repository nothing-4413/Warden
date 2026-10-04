"""Prometheus 指标（M2 监控大盘）：运行总数 + 耗时，暴露 /metrics。

失败率 = warden_runs_total{status="error"} / warden_runs_total（Grafana 面板与告警据此计算）。
LLM 调用次数 / 成本指标留到 M5（成本统计）再加，避免过早引入无谓复杂度。
"""
from __future__ import annotations

from prometheus_client import Counter, Histogram, generate_latest

RUNS_TOTAL = Counter(
    "warden_runs_total",
    "Agent/task 运行总数，按 kind/name/status 区分。",
    ["kind", "name", "status"],
)
RUN_DURATION = Histogram(
    "warden_run_duration_seconds",
    "单次运行耗时（秒）。",
    ["kind", "name"],
    buckets=(0.1, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0, 60.0, 120.0, 300.0, 600.0),
)


def record_run(kind: str, name: str, status: str, duration_s: float) -> None:
    """记录一次运行结果：kind=chat|task，status=ok|error|skipped。"""
    RUNS_TOTAL.labels(kind=kind, name=name, status=status).inc()
    RUN_DURATION.labels(kind=kind, name=name).observe(duration_s)


def metrics_text() -> str:
    out = generate_latest()
    return out.decode("utf-8") if isinstance(out, bytes) else out
