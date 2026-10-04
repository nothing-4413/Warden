"""Prometheus 指标：运行总数 + 耗时 + LLM token/成本（M2 大盘，M5 加成本）。

失败率 = warden_runs_total{status="error"} / warden_runs_total（Grafana 面板与告警据此计算）。
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
TOKENS_TOTAL = Counter(
    "warden_tokens_total",
    "LLM token 用量（累计），按 model/direction 区分。",
    ["model", "direction"],
)
COST_TOTAL = Counter(
    "warden_cost_dollars_total",
    "LLM 成本（USD，累计），按 model 区分。",
    ["model"],
)


def record_run(kind: str, name: str, status: str, duration_s: float) -> None:
    """记录一次运行结果：kind=chat|task，status=ok|error|skipped。"""
    RUNS_TOTAL.labels(kind=kind, name=name, status=status).inc()
    RUN_DURATION.labels(kind=kind, name=name).observe(duration_s)


def record_llm_usage(model: str, prompt_tokens: int, completion_tokens: int, cost: float) -> None:
    """记录一次 LLM 调用的 token 用量与折算成本（累计计数）。"""
    TOKENS_TOTAL.labels(model=model, direction="prompt").inc(prompt_tokens)
    TOKENS_TOTAL.labels(model=model, direction="completion").inc(completion_tokens)
    COST_TOTAL.labels(model=model).inc(cost)


def metrics_text() -> str:
    out = generate_latest()
    return out.decode("utf-8") if isinstance(out, bytes) else out
