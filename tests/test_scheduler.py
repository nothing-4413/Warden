"""调度层：trigger 装配、单次运行入口（落库 / 通知 / 重试兜底）与任务注册表。"""

from __future__ import annotations

import pytest

from app.config import Settings
from app.harness import STATUS_ERROR, STATUS_OK
from app.harness.run_store import RunStore
from app.notify.base import Notifier
from app.scheduler import (
    BaseTask,
    Services,
    TaskNotFoundError,
    TaskRegistry,
    build_scheduler,
    run_once,
)
from app.scheduler.runner import _build_trigger


class RecordingNotifier(Notifier):
    name = "recording"

    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    def send(self, title: str, content: str) -> None:
        self.sent.append((title, content))


class StubTask(BaseTask):
    name = "stub"
    description = "测试任务"
    schedule = {"trigger": "interval", "minutes": 5}

    def __init__(self, status: str = "ok", error: Exception | None = None) -> None:
        self.status = status
        self.error = error
        self.calls = 0

    def run(self, ctx: Services):
        from app.scheduler.base import TaskResult

        self.calls += 1
        if self.error is not None:
            raise self.error
        return TaskResult(self.name, self.status, f"{self.status} 的产出")


def _settings(**kw) -> Settings:
    base = {"retry_attempts": 1, "retry_backoff_s": 0}
    base.update(kw)
    return Settings(**base)


def _ctx(tmp_path, **kw) -> tuple[Services, RecordingNotifier, RunStore]:
    store = RunStore(str(tmp_path / "w.db"))
    notifier = RecordingNotifier()
    return Services(_settings(**kw), llm=None, notifier=notifier, store=store), notifier, store


# ---------- TaskRegistry ----------


def test_registry_register_get_names() -> None:
    reg = TaskRegistry()
    a, b = StubTask(), StubTask()
    b.name = "other"
    reg.register(a)
    reg.register(b)
    assert reg.get("stub") is a
    assert reg.names() == ["stub", "other"]
    assert reg.all() == [a, b]


def test_registry_rejects_duplicate_and_unknown() -> None:
    reg = TaskRegistry()
    reg.register(StubTask())
    with pytest.raises(ValueError, match="duplicate task name: stub"):
        reg.register(StubTask())
    with pytest.raises(TaskNotFoundError, match="unknown task: nope"):
        reg.get("nope")


# ---------- trigger 装配 ----------


def test_build_trigger_interval_and_cron() -> None:
    assert str(_build_trigger({"trigger": "interval", "minutes": 5})) == "interval[0:05:00]"
    assert "day_of_week='mon'" in str(
        _build_trigger({"trigger": "cron", "day_of_week": "mon", "hour": 9})
    )


def test_build_trigger_defaults_to_interval_and_rejects_unknown() -> None:
    assert str(_build_trigger({"minutes": 1})) == "interval[0:01:00]"
    with pytest.raises(ValueError, match="unsupported trigger type: lunar"):
        _build_trigger({"trigger": "lunar"})


def test_build_scheduler_registers_every_task(tmp_path) -> None:
    reg = TaskRegistry()
    reg.register(StubTask())
    other = StubTask()
    other.name = "second"
    reg.register(other)
    ctx, _, store = _ctx(tmp_path)

    scheduler = build_scheduler(reg, ctx)
    try:
        jobs = {j.id: j for j in scheduler.get_jobs()}
        assert set(jobs) == {"stub", "second"}
        assert jobs["stub"].max_instances == 1
    finally:
        if scheduler.running:  # 没 start 过就 shutdown 会抛 SchedulerNotRunningError
            scheduler.shutdown(wait=False)
        store.close()


# ---------- _run_task / run_once ----------


def test_run_once_persists_and_notifies(tmp_path) -> None:
    reg = TaskRegistry()
    task = StubTask()
    reg.register(task)
    ctx, notifier, store = _ctx(tmp_path)

    result = run_once(reg, ctx, "stub")

    assert result.status == STATUS_OK
    assert task.calls == 1
    assert notifier.sent == [("stub 报告", "ok 的产出")]
    record = store.list(kind="task")[0]
    assert (record.name, record.status, record.output) == ("stub", STATUS_OK, "ok 的产出")
    assert record.attempts == 1 and record.finished_at is not None
    store.close()


def test_run_once_unknown_task_raises(tmp_path) -> None:
    ctx, _, store = _ctx(tmp_path)
    with pytest.raises(TaskNotFoundError):
        run_once(TaskRegistry(), ctx, "ghost")
    store.close()


def test_run_task_error_is_swallowed_and_recorded(tmp_path) -> None:
    """任务抛异常不能掀翻调度线程：转成 error 结果并落库。"""
    reg = TaskRegistry()
    task = StubTask(error=RuntimeError("接口挂了"))
    reg.register(task)
    ctx, notifier, store = _ctx(tmp_path)

    result = run_once(reg, ctx, "stub")

    assert (result.status, result.error) == (STATUS_ERROR, "接口挂了")
    assert notifier.sent == [("stub 失败", "接口挂了")]
    record = store.list(kind="task")[0]
    assert (record.status, record.error) == (STATUS_ERROR, "接口挂了")
    store.close()


def test_backoff_retries_before_giving_up(tmp_path) -> None:
    """retry_attempts=3 时同一任务会被调用 3 次，attempts 落库。"""
    reg = TaskRegistry()
    task = StubTask(error=RuntimeError("总是失败"))
    reg.register(task)
    ctx, _, store = _ctx(tmp_path, retry_attempts=3)

    result = run_once(reg, ctx, "stub")

    assert task.calls == 3
    assert result.status == STATUS_ERROR
    assert store.list(kind="task")[0].attempts == 3
    store.close()


def test_skipped_task_is_not_notified(tmp_path) -> None:
    reg = TaskRegistry()
    reg.register(StubTask(status="skipped"))
    ctx, notifier, store = _ctx(tmp_path)

    assert run_once(reg, ctx, "stub").status == "skipped"
    assert notifier.sent == []
    store.close()


def test_runs_without_store_or_metrics(tmp_path) -> None:
    """store=None（不落库）+ metrics_enabled=False 时也要正常跑完。"""
    reg = TaskRegistry()
    reg.register(StubTask())
    notifier = RecordingNotifier()
    ctx = Services(_settings(metrics_enabled=False), llm=None, notifier=notifier, store=None)

    assert run_once(reg, ctx, "stub").status == STATUS_OK
    assert notifier.sent == [("stub 报告", "ok 的产出")]
