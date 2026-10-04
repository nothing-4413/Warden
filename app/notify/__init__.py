"""通知器层：报告送达方式。"""
from __future__ import annotations

from ..config import Settings
from .base import Notifier
from .console import ConsoleNotifier
from .file import FileNotifier

__all__ = ["Notifier", "ConsoleNotifier", "FileNotifier", "get_notifier"]


def get_notifier(settings: Settings) -> Notifier:
    """按 settings.notify_kind 选择通知器（M1：console / file）。"""
    if settings.notify_kind.lower() == "file":
        return FileNotifier(settings.report_dir)
    return ConsoleNotifier()
