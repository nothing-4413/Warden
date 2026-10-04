"""每日资讯/论文简报：抓 RSS → 去重（跨次运行） → LLM 摘要 → 通知。"""
from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

import httpx

from ..scheduler.base import BaseTask, Services, TaskResult

_ATOM = {"a": "http://www.w3.org/2005/Atom"}
_MAX_SEEN = 500  # 已见链接只保留最近 500 条，避免无限增长


@dataclass
class FeedItem:
    title: str
    link: str
    summary: str = ""
    published: str = ""

    def short(self, limit: int = 120) -> str:
        body = " ".join((self.summary or self.title).split())
        return body[:limit]


def _text(el) -> str:
    return (el.text or "").strip() if el is not None else ""


def parse_feed(xml_text: str) -> list[FeedItem]:
    """解析 RSS 2.0（<item>）与 Atom（<entry>）两种格式，stdlib 实现，不引 feedparser。"""
    root = ET.fromstring(xml_text)
    items: list[FeedItem] = []

    for it in root.findall(".//item"):
        items.append(
            FeedItem(
                _text(it.find("title")),
                _text(it.find("link")),
                _text(it.find("description")),
                _text(it.find("pubDate")),
            )
        )

    for en in root.findall(".//a:entry", _ATOM):
        link = ""
        for ln in en.findall("a:link", _ATOM):
            href = ln.get("href")
            if href and ln.get("rel") in (None, "", "alternate"):
                link = href
                break
        if not link:
            link = next((ln.get("href") or "" for ln in en.findall("a:link", _ATOM)), "")
        items.append(
            FeedItem(
                _text(en.find("a:title", _ATOM)),
                link,
                _text(en.find("a:summary", _ATOM)),
                _text(en.find("a:updated", _ATOM)),
            )
        )

    return [i for i in items if i.title and i.link]


class NewsDigestTask(BaseTask):
    name = "news_digest"
    description = "抓取 RSS 资讯/论文，去重后由 LLM 生成简报"
    schedule = {"trigger": "cron", "hour": 8, "minute": 0}

    def __init__(self, rss_sources: list[str], data_dir: str) -> None:
        self._sources = rss_sources
        self._seen_path = Path(data_dir) / "news_seen.json"

    def _load_seen(self) -> set[str]:
        try:
            return set(json.loads(self._seen_path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            return set()

    def _save_seen(self, seen: set[str]) -> None:
        self._seen_path.parent.mkdir(parents=True, exist_ok=True)
        self._seen_path.write_text(
            json.dumps(list(seen)[-_MAX_SEEN:], ensure_ascii=False), encoding="utf-8"
        )

    def _fetch_all(self) -> tuple[list[FeedItem], list[str]]:
        items: list[FeedItem] = []
        errors: list[str] = []
        with httpx.Client(timeout=15.0, follow_redirects=True) as client:
            for src in self._sources:
                try:
                    resp = client.get(src)
                    resp.raise_for_status()
                    items.extend(parse_feed(resp.text))
                except Exception as exc:  # 单个源失败不拖垮整体
                    errors.append(f"{src}: {exc}")
        return items, errors

    def run(self, ctx: Services) -> TaskResult:
        items, errors = self._fetch_all()
        seen = self._load_seen()
        fresh = [i for i in items if i.link not in seen]
        if not fresh:
            if errors:
                return TaskResult(self.name, "error", "抓取失败", {"errors": errors})
            return TaskResult(self.name, "skipped", "没有新条目", {"total": len(items), "new": 0})

        digest = ctx.llm.chat(self._prompt(fresh))
        self._save_seen(seen | {i.link for i in fresh})
        return TaskResult(
            self.name, "ok", digest,
            {"total": len(items), "new": len(fresh), "errors": errors},
        )

    def _prompt(self, items: list[FeedItem]) -> list[dict]:
        lines = [
            f"- [{i.title}]({i.link})" + (f" — {i.short()}" if i.summary else "")
            for i in items[:30]
        ]
        system = (
            "你是个人资讯助理。把下面的新条目汇总成一份简洁的中文简报："
            "按主题归类，每条一句话点出核心信息，最后给出 3 条最值得关注的重点。直接输出 Markdown。"
        )
        user = "今日新条目：\n" + "\n".join(lines)
        return [{"role": "system", "content": system}, {"role": "user", "content": user}]
