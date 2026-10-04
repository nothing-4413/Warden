"""通知器：任务产出的报告如何送达。M1 提供 console + file 两种。"""
from __future__ import annotations

from abc import ABC, abstractmethod


class Notifier(ABC):
    name: str = "base"

    @abstractmethod
    def send(self, title: str, content: str) -> None:
        ...
