"""每周复盘：汇总近 7 天笔记（含正文）+ 提交记录 → LLM 生成周报。"""

from __future__ import annotations

import subprocess
from datetime import datetime, timedelta
from pathlib import Path

from ..scheduler.base import BaseTask, Services, TaskResult

# 笔记正文进 prompt 的上限：单篇 800 字、整体 6000 字。
# 只给文件名的话模型只能瞎编（"本周推进了什么"），给全文又会把上下文挤爆——
# 这两个数是折中：新笔记优先，超了就截断并显式标注，而不是静默丢内容。
_MAX_CHARS_PER_NOTE = 800
_MAX_CHARS_TOTAL = 6000


class WeeklyReviewTask(BaseTask):
    name = "weekly_review"
    description = "汇总本周笔记/提交生成周报"
    schedule = {"trigger": "cron", "day_of_week": "mon", "hour": 9, "minute": 0}

    def __init__(self, notes_dir: str, repo_path: str) -> None:
        self._notes_dir = Path(notes_dir) if notes_dir else None
        self._repo = Path(repo_path)

    def _recent_notes(
        self,
        days: int = 7,
        per_note_chars: int = _MAX_CHARS_PER_NOTE,
        total_chars: int = _MAX_CHARS_TOTAL,
    ) -> list[str]:
        """近 N 天改过的 Markdown，带上**正文**（不是只给文件名）。

        mtime 是"最近改动"的近似：改内容或 touch 都算，正好符合"这周动过的笔记"。
        排序按 mtime 倒序，正文超长按篇截断、总量按整体截断（最新优先），
        保证 prompt 里既有真实内容，又不会因为笔记多而无限膨胀。
        """
        if self._notes_dir is None or not self._notes_dir.is_dir():
            return []
        cutoff = datetime.now() - timedelta(days=days)
        found: list[tuple[float, str]] = []
        for p in self._notes_dir.rglob("*.md"):
            try:
                mtime = p.stat().st_mtime
                text = p.read_text(encoding="utf-8", errors="replace").strip()
            except OSError:
                continue
            if not text or datetime.fromtimestamp(mtime) < cutoff:
                continue
            body = text
            if len(body) > per_note_chars:
                body = body[:per_note_chars].rstrip() + "\n……（笔记过长，已截断）"
            stamp = datetime.fromtimestamp(mtime)
            found.append((mtime, f"### {p.name}（改于 {stamp:%Y-%m-%d %H:%M}）\n{body}"))

        found.sort(key=lambda pair: pair[0], reverse=True)  # 最近改的排前面
        out: list[str] = []
        used = 0
        for _, block in found:
            if out and used + len(block) > total_chars:
                break
            out.append(block)
            used += len(block)
        return out

    def _recent_commits(self, days: int = 7) -> list[str]:
        try:
            r = subprocess.run(
                # safe.directory：容器里 /repo 是宿主机 bind mount，uid 与容器用户不一致，
                # 不加这条 git 会报 "detected dubious ownership" 并整段返回空。
                [
                    "git",
                    "-c",
                    f"safe.directory={self._repo.resolve()}",
                    "log",
                    "--oneline",
                    f"--since={days} days ago",
                ],
                cwd=self._repo,
                capture_output=True,
                text=True,
                # 不指定编码时 Windows 按本地代码页（GBK）解码：含中文的提交信息会在
                # subprocess 读取线程里抛 UnicodeDecodeError，整段 git log 被静默丢弃。
                encoding="utf-8",
                errors="replace",
                timeout=15,
            )
            return [ln for ln in r.stdout.splitlines() if ln.strip()]
        except Exception:  # 非 git 仓库 / git 不可用时静默降级
            return []

    def run(self, ctx: Services) -> TaskResult:
        notes = self._recent_notes()
        commits = self._recent_commits()
        report = ctx.llm.chat(self._prompt(notes, commits))
        return TaskResult(
            self.name,
            "ok",
            report,
            {"notes": len(notes), "commits": len(commits)},
        )

    def _prompt(self, notes: list[str], commits: list[str]) -> list[dict]:
        system = (
            "你是个人复盘助手。根据下面提供的本周笔记正文与提交记录，生成一份中文周报："
            "总结本周做了什么、有哪些进展与收获、下周可以推进什么。"
            "只依据给到的内容，不要编造没有出现过的项目或结论。直接输出 Markdown。"
        )
        user = (
            "## 本周笔记\n"
            + ("\n\n".join(notes) or "（无）")
            + "\n\n## 本周提交\n"
            + ("\n".join(f"- {c}" for c in commits[:50]) or "（无）")
        )
        return [{"role": "system", "content": system}, {"role": "user", "content": user}]
