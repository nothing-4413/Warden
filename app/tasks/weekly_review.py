"""每周复盘：汇总近 7 天笔记 + 提交记录 → LLM 生成周报。"""
from __future__ import annotations

import subprocess
from datetime import datetime, timedelta
from pathlib import Path

from ..scheduler.base import BaseTask, Services, TaskResult


class WeeklyReviewTask(BaseTask):
    name = "weekly_review"
    description = "汇总本周笔记/提交生成周报"
    schedule = {"trigger": "cron", "day_of_week": "mon", "hour": 9, "minute": 0}

    def __init__(self, notes_dir: str, repo_path: str) -> None:
        self._notes_dir = Path(notes_dir) if notes_dir else None
        self._repo = Path(repo_path)

    def _recent_notes(self, days: int = 7) -> list[str]:
        if self._notes_dir is None or not self._notes_dir.is_dir():
            return []
        cutoff = datetime.now() - timedelta(days=days)
        out: list[str] = []
        for p in sorted(self._notes_dir.rglob("*.md")):
            try:
                mt = datetime.fromtimestamp(p.stat().st_mtime)
            except OSError:
                continue
            if mt >= cutoff:
                out.append(f"- {p.name}（改于 {mt:%Y-%m-%d %H:%M}）")
        return out

    def _recent_commits(self, days: int = 7) -> list[str]:
        try:
            r = subprocess.run(
                ["git", "log", "--oneline", f"--since={days} days ago"],
                cwd=self._repo, capture_output=True, text=True, timeout=15,
            )
            return [ln for ln in r.stdout.splitlines() if ln.strip()]
        except Exception:  # 非 git 仓库 / git 不可用时静默降级
            return []

    def run(self, ctx: Services) -> TaskResult:
        notes = self._recent_notes()
        commits = self._recent_commits()
        report = ctx.llm.chat(self._prompt(notes, commits))
        return TaskResult(
            self.name, "ok", report,
            {"notes": len(notes), "commits": len(commits)},
        )

    def _prompt(self, notes: list[str], commits: list[str]) -> list[dict]:
        system = (
            "你是个人复盘助手。根据下面提供的本周笔记与提交记录，生成一份中文周报："
            "总结本周做了什么、有哪些进展与收获、下周可以推进什么。直接输出 Markdown。"
        )
        user = (
            "## 本周笔记\n" + ("\n".join(notes) or "（无）")
            + "\n\n## 本周提交\n" + ("\n".join(f"- {c}" for c in commits[:50]) or "（无）")
        )
        return [{"role": "system", "content": system}, {"role": "user", "content": user}]
