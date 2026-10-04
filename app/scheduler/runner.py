"""调度器运行器：APScheduler 装配 + 单次运行入口。

时间用本地时区（stdlib 取 tzinfo，不引入 tzlocal/pytz）。
"""
from __future__ import annotations

import logging
from datetime import datetime

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

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
    log.info("task start: %s", task.name)
    try:
        result = task.run(ctx)
    except Exception as exc:  # 任务异常兜底，不让调度线程崩溃
        log.exception("task failed: %s", task.name)
        result = TaskResult(task=task.name, status="error", summary=str(exc), error=str(exc))
    _notify(task, ctx, result)
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
