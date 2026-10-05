"""内置定时任务 + 默认注册表构建。"""

from __future__ import annotations

from ..config import Settings
from ..scheduler import TaskRegistry
from .news_digest import NewsDigestTask
from .repo_report import RepoReportTask
from .weekly_review import WeeklyReviewTask

__all__ = [
    "NewsDigestTask",
    "RepoReportTask",
    "WeeklyReviewTask",
    "build_default_task_registry",
]


def build_default_task_registry(settings: Settings) -> TaskRegistry:
    """构建 M1 默认任务注册表（3 个任务）。任务构造只吃配置，LLM/通知器经 Services 传入。"""
    reg = TaskRegistry()
    reg.register(NewsDigestTask(settings.rss_source_list, settings.data_dir))
    reg.register(RepoReportTask(settings.repo_path))
    reg.register(WeeklyReviewTask(settings.notes_dir, settings.repo_path))
    return reg
