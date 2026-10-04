"""任务基类与运行结果。"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from ..config import Settings
from ..llm import LLMClient
from ..notify import Notifier


@dataclass
class TaskResult:
    task: str
    status: str  # "ok" | "skipped" | "error"
    summary: str
    detail: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


@dataclass
class Services:
    """任务运行所需的共享服务（M1 只读上下文；M2 加持久化/追踪后扩展）。"""
    settings: Settings
    llm: LLMClient
    notifier: Notifier


class BaseTask(ABC):
    """所有定时任务的统一入口：run(ctx) -> TaskResult。

    schedule 是 APScheduler trigger 配置，如 {"trigger": "cron", "hour": 8}
    或 {"trigger": "interval", "minutes": 60}。
    """

    name: str = ""
    description: str = ""
    schedule: dict = {"trigger": "interval", "minutes": 60}

    @abstractmethod
    def run(self, ctx: Services) -> TaskResult:
        ...
