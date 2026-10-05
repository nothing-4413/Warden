"""代码库维护助手：扫描 TODO/技术债 + 依赖版本 + 近期提交 → LLM 报告。"""

from __future__ import annotations

import re
import subprocess
import tomllib
from importlib import metadata as importlib_metadata
from pathlib import Path

from ..scheduler.base import BaseTask, Services, TaskResult

_SCAN_SUFFIXES = (".py",)
_MARKERS = ("TODO", "FIXME", "HACK", "XXX")
_SKIP_DIRS = {".venv", "node_modules", ".git", "__pycache__"}


class RepoReportTask(BaseTask):
    name = "repo_report"
    description = "扫描代码库产出 TODO/技术债/依赖升级报告"
    schedule = {"trigger": "interval", "hours": 24}

    def __init__(self, repo_path: str) -> None:
        self._root = Path(repo_path)

    def _scan_todos(self) -> list[str]:
        hits: list[str] = []
        for p in sorted(self._root.rglob("*")):
            if not p.is_file() or p.suffix not in _SCAN_SUFFIXES:
                continue
            if _SKIP_DIRS & set(p.parts):
                continue
            try:
                text = p.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            for i, line in enumerate(text.splitlines(), 1):
                if any(m in line for m in _MARKERS):
                    hits.append(f"{p}:{i}: {line.strip()}")
        return hits

    def _declared_deps(self) -> list[str]:
        """从 pyproject.toml 读声明依赖名（tomllib 为 3.11+ stdlib）。"""
        try:
            data = tomllib.loads((self._root / "pyproject.toml").read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError):
            return []
        return [self._dep_name(d) for d in data.get("project", {}).get("dependencies", [])]

    @staticmethod
    def _dep_name(dep: str) -> str:
        """从 "uvicorn[standard]>=0.30" 这类声明里只取包名 "uvicorn"。"""
        dep = dep.strip()
        if "[" in dep:  # 去掉 extras 部分
            dep = dep.split("[", 1)[0]
        return re.split(r"[<>=!~;,\s]", dep, maxsplit=1)[0].strip()

    def _deps(self) -> list[str]:
        out: list[str] = []
        for name in self._declared_deps():
            try:
                out.append(f"{name}=={importlib_metadata.version(name)}")
            except importlib_metadata.PackageNotFoundError:
                out.append(f"{name}==(未安装)")
        return out

    def _recent_commits(self, days: int = 7) -> list[str]:
        try:
            r = subprocess.run(
                ["git", "log", "--oneline", f"--since={days} days ago"],
                cwd=self._root,
                capture_output=True,
                text=True,
                timeout=15,
            )
            return [ln for ln in r.stdout.splitlines() if ln.strip()]
        except Exception:  # 非 git 仓库 / git 不可用时静默降级
            return []

    def run(self, ctx: Services) -> TaskResult:
        todos = self._scan_todos()
        deps = self._deps()
        commits = self._recent_commits()
        report = ctx.llm.chat(self._prompt(todos, deps, commits))
        return TaskResult(
            self.name,
            "ok",
            report,
            {"todos": len(todos), "deps": len(deps), "commits": len(commits)},
        )

    def _prompt(self, todos: list[str], deps: list[str], commits: list[str]) -> list[dict]:
        system = (
            "你是代码库维护助手。根据下面提供的 TODO、依赖、近期提交信息，生成一份技术债与维护报告："
            "按优先级列出需要处理的问题，指出可以升级的依赖，最后给出维护建议。直接输出 Markdown。"
        )
        user = (
            "## TODO/FIXME/HACK\n"
            + ("\n".join(f"- `{t}`" for t in todos[:50]) or "（无）")
            + "\n\n## 依赖\n"
            + ("\n".join(f"- {d}" for d in deps) or "（无）")
            + "\n\n## 近期提交\n"
            + ("\n".join(f"- {c}" for c in commits[:30]) or "（无）")
        )
        return [{"role": "system", "content": system}, {"role": "user", "content": user}]
