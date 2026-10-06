"""周报任务：近 7 天笔记与提交的收集（含 git 不可用时的降级）与提示词组装。"""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

import pytest

from app.config import Settings
from app.notify.base import Notifier
from app.scheduler.base import Services
from app.tasks.repo_report import RepoReportTask
from app.tasks.weekly_review import WeeklyReviewTask


class FakeLLM:
    model = "fake"

    def __init__(self) -> None:
        self.prompts: list[list[dict]] = []

    def chat(self, messages, temperature=None):
        self.prompts.append(messages)
        return "## 周报\n- 本周推进了评测"


class RecordingNotifier(Notifier):
    name = "recording"

    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    def send(self, title: str, content: str) -> None:
        self.sent.append((title, content))


def _write(path: Path, text: str, *, age_days: float = 0.0) -> None:
    path.write_text(text, encoding="utf-8")
    if age_days:
        old = time.time() - age_days * 86400
        os.utime(path, (old, old))


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.email=t@example.com", "-c", "user.name=t", *args],
        cwd=repo,
        check=True,
        capture_output=True,
    )


def test_recent_notes_only_keeps_last_seven_days(tmp_path) -> None:
    notes = tmp_path / "notes"
    (notes / "sub").mkdir(parents=True)
    _write(notes / "new.md", "本周写的")
    _write(notes / "sub" / "newer.md", "也是本周")
    _write(notes / "old.md", "上周写的", age_days=30)

    task = WeeklyReviewTask(str(notes), str(tmp_path))
    listed = task._recent_notes()

    assert len(listed) == 2
    assert any("new.md" in line and "改于" in line for line in listed)
    assert all("old.md" not in line for line in listed)


def test_recent_notes_handles_missing_or_unset_dir(tmp_path) -> None:
    assert WeeklyReviewTask("", str(tmp_path))._recent_notes() == []  # 未配置
    assert (
        WeeklyReviewTask(str(tmp_path / "nope"), str(tmp_path))._recent_notes() == []
    )  # 目录不存在


def test_recent_commits_reads_real_repo(tmp_path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _write(repo / "a.txt", "1")
    _git(repo, "add", "a.txt")
    _git(repo, "commit", "-m", "feat: 第一件事")

    lines = WeeklyReviewTask("", str(repo))._recent_commits()

    assert any("feat: 第一件事" in line for line in lines)


def test_recent_commits_degrades_silently_outside_git(tmp_path, monkeypatch) -> None:
    # git 不在 PATH（或调用炸了）时返回空列表，不能把周报任务带崩
    def boom(*args, **kwargs):
        raise OSError("git not found")

    monkeypatch.setattr("app.tasks.weekly_review.subprocess.run", boom)
    assert WeeklyReviewTask("", str(tmp_path))._recent_commits() == []
    monkeypatch.undo()
    # 目录根本不存在时 git 也会失败（注意：tmp_path 本身在仓库里，所以不能用它测「非仓库」）
    assert WeeklyReviewTask("", str(tmp_path / "missing"))._recent_commits() == []


@pytest.mark.parametrize(
    "make_task",
    [lambda root: WeeklyReviewTask("", root), RepoReportTask],
    ids=["weekly_review", "repo_report"],
)
def test_chinese_commit_message_survives_subprocess_decode(tmp_path, make_task) -> None:
    """回归：subprocess 不指定编码时 Windows 用 GBK 解码，中文提交信息会整段丢失。

    两个任务各自有一份同样的 `git log` 调用，这里一起钉住。
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _write(repo / "a.txt", "1")
    _git(repo, "add", "a.txt")
    _git(repo, "commit", "-m", "feat: 中文提交信息不能丢")

    lines = make_task(str(repo))._recent_commits()

    assert any("feat: 中文提交信息不能丢" in line for line in lines)


def test_run_reports_counts_and_prompt(tmp_path) -> None:
    notes = tmp_path / "notes"
    notes.mkdir()
    _write(notes / "today.md", "今天做的事")
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _write(repo / "a.txt", "1")
    _git(repo, "add", "a.txt")
    _git(repo, "commit", "-m", "feat: 提交一")

    llm = FakeLLM()
    task = WeeklyReviewTask(str(notes), str(repo))
    result = task.run(Services(Settings(), llm, RecordingNotifier()))

    assert result.status == "ok"
    assert result.summary.startswith("## 周报")
    assert result.detail == {"notes": 1, "commits": 1}
    system, user = llm.prompts[0]
    assert system["role"] == "system" and "个人复盘助手" in system["content"]
    assert "## 本周笔记" in user["content"] and "today.md" in user["content"]
    assert "## 本周提交" in user["content"] and "feat: 提交一" in user["content"]


def test_prompt_marks_empty_sections(tmp_path) -> None:
    task = WeeklyReviewTask("", str(tmp_path))
    prompt = task._prompt([], [])
    assert "（无）" in prompt[1]["content"]


def test_prompt_truncates_commit_list_to_fifty(tmp_path) -> None:
    task = WeeklyReviewTask("", str(tmp_path))
    commits = [f"c{i}" for i in range(60)]
    user = task._prompt([], commits)[1]["content"]
    assert "c49" in user and "c50" not in user
