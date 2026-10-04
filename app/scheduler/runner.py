"""调度器运行器：APScheduler 装配 + 单次运行入口（含 trace_id/持久化/重试）。

时间用本地时区（stdlib 取 tzinfo，不引入 tzlocal/pytz）。
"""
from __future__ import annotations

import logging
import time
from datetime import datetime

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from ..harness import RunRecord, new_trace_id, record_run, trace_span, with_retry
from .base import BaseTask, Services, TaskResult
from .registry import TaskRegistry

log = logging.getLogger(__name__)


def _build_trigger(schedule: dict):
    spec = dict(schedule)
    kind = spec.pop("trigger", "interval")
    if kind == "cron":
        return CronTrigger(**spec)
    if kind == "interval":
        return IntervalTrigger(**spec)
    raise ValueError(f"unsupported trigger type: {kind}")


def build_scheduler(registry: TaskRegistry, ctx: Services) -> BackgroundScheduler:
    """把注册表里的每个任务按 schedule 挂到后台调度器。"""
    scheduler = BackgroundScheduler(timezone=datetime.now().astimezone().tzinfo)
    for task in registry.all():
        scheduler.add_job(
            _run_task,
            trigger=_build_trigger(task.schedule),
            args=[task, ctx],
            id=task.name,
            name=task.name,
            coalesce=True,
            max_instances=1,
        )
        log.info("scheduled %s: %s", task.name, task.schedule)
    return scheduler


def _run_task(task: BaseTask, ctx: Services) -> TaskResult:
    tid = new_trace_id()
    started = time.time()
    with trace_span(tid):
        log.info("task start: %s", task.name)
        record = None
        if ctx.store is not None:
            record = RunRecord(id=tid, kind="task", name=task.name)
            ctx.store.start(record)

        attempts: list[int] = []
        try:
            result = with_retry(
                lambda: task.run(ctx),
                attempts=ctx.settings.retry_attempts,
                backoff_s=ctx.settings.retry_backoff_s,
                on_attempt=attempts.append,
            )
        except Exception as exc:  # 重试仍失败：兜底，不让调度线程崩溃
            log.exception("task failed: %s", task.name)
            result = TaskResult(task=task.name, status="error", summary=str(exc), error=str(exc))

        if record is not None:
            record.status = result.status
            record.output = result.summary
            record.error = result.error
            record.attempts = len(attempts)
            record.finished_at = time.time()
            ctx.store.update(record)

        _notify(task, ctx, result)
        if ctx.settings.metrics_enabled:
            record_run("task", task.name, result.status, time.time() - started)
        log.info("task done: %s -> %s", task.name, result.status)
        return result


def _notify(task: BaseTask, ctx: Services, result: TaskResult) -> None:
    """统一送达：ok 送报告正文，error 送失败原因，skipped 静默。"""
    if result.status == "ok":
        ctx.notifier.send(f"{task.name} 报告", result.summary)
    elif result.status == "error":
        ctx.notifier.send(f"{task.name} 失败", result.summary)


def run_once(registry: TaskRegistry, ctx: Services, name: str) -> TaskResult:
    """手动触发单次运行（API / CLI 用）。"""
    return _run_task(registry.get(name), ctx)
