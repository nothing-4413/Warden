"""文件通知器：把报告写成 Markdown 存到 report_dir。"""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from .base import Notifier


class FileNotifier(Notifier):
    name = "file"

    def __init__(self, report_dir: str) -> None:
        self._dir = Path(report_dir)

    def send(self, title: str, content: str) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)
        slug = re.sub(r"[^\w\-]+", "_", title, flags=re.UNICODE).strip("_")[:60].lower()
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = self._dir / f"{slug}_{ts}.md"
        path.write_text(f"# {title}\n\n{content}\n", encoding="utf-8")
        print(f"[file-notifier] wrote {path}")
