"""任务注册表：按名字注册/查找任务（与工具注册表同构，插件化）。"""
from __future__ import annotations

from .base import BaseTask


class TaskNotFoundError(Exception):
    pass


class TaskRegistry:
    def __init__(self) -> None:
        self._tasks: dict[str, BaseTask] = {}

    def register(self, task: BaseTask) -> None:
        if task.name in self._tasks:
            raise ValueError(f"duplicate task name: {task.name}")
        self._tasks[task.name] = task

    def get(self, name: str) -> BaseTask:
        try:
            return self._tasks[name]
        except KeyError:
            raise TaskNotFoundError(f"unknown task: {name}") from None

    def all(self) -> list[BaseTask]:
        return list(self._tasks.values())

    def names(self) -> list[str]:
        return list(self._tasks)
