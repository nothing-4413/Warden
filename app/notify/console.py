"""控制台通知器：直接打印。适合调试与 cron 日志。"""
from __future__ import annotations

from .base import Notifier


class ConsoleNotifier(Notifier):
    name = "console"

    def send(self, title: str, content: str) -> None:
        print(f"\n===== {title} =====\n{content}\n")
