"""用假 LLM / 假网络验证三个定时任务的核心逻辑（不发真实网络请求）。"""

import app.tasks.news_digest as news_digest
from app.config import Settings
from app.notify.base import Notifier
from app.scheduler.base import Services
from app.tasks import build_default_task_registry
from app.tasks.news_digest import NewsDigestTask, parse_feed
from app.tasks.repo_report import RepoReportTask
from app.tasks.weekly_review import WeeklyReviewTask


class FakeLLM:
    model = "fake"

    def chat(self, messages, temperature=None):
        return "## 简报\n- 这是假模型生成的摘要"


class RecordingNotifier(Notifier):
    name = "recording"

    def __init__(self):
        self.sent = []

    def send(self, title, content):
        self.sent.append((title, content))


RSS_XML = """<?xml version="1.0"?>
<rss version="2.0"><channel><title>t</title>
<item><title>One</title><link>http://a/1</link><description>first</description><pubDate>Tue, 01 Jan 2025</pubDate></item>
<item><title>Two</title><link>http://a/2</link><description>second</description></item>
</channel></rss>"""

ATOM_XML = """<?xml version="1.0"?>
<feed xmlns="http://www.w3.org/2005/Atom">
<entry><title>Paper A</title><link href="http://b/1" rel="alternate"/><summary>abstract</summary><updated>2025-01-01T00:00:00Z</updated></entry>
<entry><title>Paper B</title><link href="http://b/2"/><updated>2025-01-02T00:00:00Z</updated></entry>
</feed>"""


class FakeResponse:
    def __init__(self, text):
        self.text = text

    def raise_for_status(self):
        pass


class FakeClient:
    def __init__(self, *a, **k):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get(self, url):
        return FakeResponse(RSS_XML)


def _ctx():
    return Services(Settings(), FakeLLM(), RecordingNotifier())


def test_parse_feed_rss():
    items = parse_feed(RSS_XML)
    assert [i.title for i in items] == ["One", "Two"]
    assert items[0].link == "http://a/1"


def test_parse_feed_atom():
    items = parse_feed(ATOM_XML)
    assert [i.title for i in items] == ["Paper A", "Paper B"]
    assert items[0].link == "http://b/1"


def test_news_digest_ok_then_skipped(monkeypatch, tmp_path):
    monkeypatch.setattr(news_digest.httpx, "Client", FakeClient)
    task = NewsDigestTask(["http://x/rss"], str(tmp_path))
    ctx = _ctx()

    r1 = task.run(ctx)
    assert r1.status == "ok"
    assert r1.detail["new"] == 2
    assert r1.detail["sources"] == {"http://x/rss": 2}

    r2 = task.run(ctx)  # 已见链接 → 跳过
    assert r2.status == "skipped"
    assert r2.detail["new"] == 0


def test_news_digest_reports_empty_source(monkeypatch, tmp_path):
    """某个源这次 0 条（arXiv 周末不更新就是）要在 summary/detail 里说出来，
    否则只看到一句「没有新条目」，分不清是没更新还是抓取坏了。"""

    class EmptyFeedClient(FakeClient):
        def get(self, url):
            return FakeResponse("<rss version='2.0'><channel><title>t</title></channel></rss>")

    monkeypatch.setattr(news_digest.httpx, "Client", EmptyFeedClient)
    task = NewsDigestTask(["http://empty/rss"], str(tmp_path))

    r = task.run(_ctx())

    assert r.status == "skipped"
    assert r.detail["sources"] == {"http://empty/rss": 0}
    assert "没返回条目" in r.summary


def test_news_seen_keeps_insertion_order_and_caps(monkeypatch, tmp_path):
    """已见列表按写入顺序保存：封顶时丢最旧的那批，不会把老链接放回来当新闻。"""
    task = NewsDigestTask(["http://x/rss"], str(tmp_path))
    task._save_seen([f"http://a/{i}" for i in range(news_digest._MAX_SEEN + 5)])

    seen = task._load_seen()

    assert len(seen) == news_digest._MAX_SEEN
    assert seen[0] == "http://a/5"  # 最旧的 5 条被丢掉
    assert seen[-1] == f"http://a/{news_digest._MAX_SEEN + 4}"


def test_repo_report_ok(monkeypatch):
    task = RepoReportTask(".")
    monkeypatch.setattr(task, "_recent_commits", lambda days=7: [])
    r = task.run(_ctx())
    assert r.status == "ok"
    assert r.detail["deps"] >= 4  # M0 4 个 + apscheduler


def test_weekly_review_ok(monkeypatch, tmp_path):
    task = WeeklyReviewTask("", ".")
    monkeypatch.setattr(task, "_recent_commits", lambda days=7: [])
    r = task.run(_ctx())
    assert r.status == "ok"
    assert r.detail["notes"] == 0


def test_build_default_task_registry():
    reg = build_default_task_registry(Settings())
    assert reg.names() == ["news_digest", "repo_report", "weekly_review"]
