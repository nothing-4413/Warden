"""HTTP 层：健康检查、指标、控制台数据与对话/任务端点（Agent、调度器、库都被打桩）。"""

from datetime import datetime

from fastapi.testclient import TestClient

import app.main as main
from app.config import Settings
from app.harness import STATUS_OK


class FakeAgent:
    """冒充 ReactAgent：只提供端点用到的 run() 返回值形状。"""

    def __init__(self) -> None:
        self.calls = 0

    def run(self, messages, trace_id=None):
        self.calls += 1

        class _Step:
            index = 0
            thought = "想一想"
            action = "calculator"
            action_input = {"expression": "2+2"}
            observation = 4

        class _Result:
            answer = "4"
            agent = "react"
            model = "fake"
            steps = [_Step()]
            self_eval = {"confidence": 0.9}

        return _Result()


class FakeRecord:
    id = "run-1"
    kind = "chat"
    name = "react"
    status = STATUS_OK
    attempts = 1
    started_at = 1_700_000_000.0
    finished_at = None
    error = None
    output = "缓存的回答"


def test_health_reports_model_tools_and_tasks() -> None:
    body = TestClient(main.app).get("/health").json()
    assert body["status"] == "ok"
    assert body["model"] and "calculator" in body["tools"] and "news_digest" in body["tasks"]


def test_metrics_endpoint_serves_prometheus_text() -> None:
    resp = TestClient(main.app).get("/metrics")
    assert resp.status_code == 200
    assert "text/plain" in resp.headers["content-type"]


def test_metrics_endpoint_404_when_disabled(monkeypatch) -> None:
    monkeypatch.setattr(main, "settings", Settings(metrics_enabled=False))
    resp = TestClient(main.app).get("/metrics")
    assert resp.status_code == 404


def test_runs_endpoint_formats_timestamps(monkeypatch) -> None:
    monkeypatch.setattr(main.store, "list", lambda limit=20: [FakeRecord()])
    body = TestClient(main.app).get("/api/v1/runs?limit=1").json()

    expected = datetime.fromtimestamp(FakeRecord.started_at).isoformat(timespec="seconds")
    assert body[0]["id"] == "run-1"
    assert body[0]["started_at"] == expected  # 秒级本地时间 ISO 串
    assert body[0]["finished_at"] is None  # 未结束的 run 给 null，不是 0 时间戳


def test_chat_endpoint_maps_steps(monkeypatch) -> None:
    agent = FakeAgent()
    monkeypatch.setitem(main._agents, "react", agent)
    monkeypatch.setattr(main.store, "get", lambda rid: None)

    body = (
        TestClient(main.app)
        .post("/api/v1/chat", json={"messages": [{"role": "user", "content": "2+2?"}]})
        .json()
    )

    assert body["answer"] == "4"
    assert body["steps"][0]["action"] == "calculator"
    assert body["self_eval"] == {"confidence": 0.9}
    assert agent.calls == 1


def test_chat_endpoint_replays_cached_answer(monkeypatch) -> None:
    """同 request_id 且上次成功：直接回放，不再调 Agent（省一次 LLM 调用）。"""
    agent = FakeAgent()
    monkeypatch.setitem(main._agents, "react", agent)
    monkeypatch.setattr(main.store, "get", lambda rid: FakeRecord())

    body = (
        TestClient(main.app)
        .post(
            "/api/v1/chat",
            json={
                "messages": [{"role": "user", "content": "2+2?"}],
                "request_id": "run-1",
            },
        )
        .json()
    )

    assert body["answer"] == "缓存的回答" and body["steps"] == []
    assert agent.calls == 0


def test_tasks_endpoint_lists_registry() -> None:
    body = TestClient(main.app).get("/api/v1/tasks").json()
    assert {t["name"] for t in body} >= {"news_digest", "repo_report", "weekly_review"}
    assert all("schedule" in t for t in body)


def test_run_task_endpoint_returns_result(monkeypatch) -> None:
    class _Result:
        task = "news_digest"
        status = "ok"
        summary = "3 条新资讯"
        detail = {"items": 3}
        error = None

    monkeypatch.setattr(main, "run_once", lambda reg, ctx, name: _Result())
    resp = TestClient(main.app).post("/api/v1/tasks/news_digest/run")

    assert resp.status_code == 200
    assert resp.json()["summary"] == "3 条新资讯"


def test_run_task_endpoint_404_for_unknown_task(monkeypatch) -> None:
    def boom(reg, ctx, name):
        raise main.TaskNotFoundError(f"unknown task: {name}")

    monkeypatch.setattr(main, "run_once", boom)
    resp = TestClient(main.app).post("/api/v1/tasks/nope/run")

    assert resp.status_code == 404
    assert "unknown task: nope" in resp.json()["detail"]


def test_lifespan_starts_and_stops_scheduler(monkeypatch) -> None:
    """应用启动时拉起调度器、退出时关掉（不真起 APScheduler，避免用例间互相影响）。"""
    events: list[str] = []

    class FakeScheduler:
        def start(self) -> None:
            events.append("start")

        def shutdown(self, wait: bool = False) -> None:
            events.append("shutdown")

    monkeypatch.setattr(main, "build_scheduler", lambda reg, ctx: FakeScheduler())

    with TestClient(main.app) as client:
        assert client.get("/health").status_code == 200

    assert events == ["start", "shutdown"]
