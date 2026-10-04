"""M2 Part1：RunStore（SQLite 持久化）+ trace_id 追踪。"""
from app.harness import (
    RunRecord,
    RunStore,
    STATUS_ERROR,
    STATUS_OK,
    STATUS_RUNNING,
    get_trace_id,
    new_trace_id,
    trace_span,
)


def test_run_store_roundtrip(tmp_path):
    store = RunStore(str(tmp_path / "warden.db"))
    run = RunRecord(id="abc123", kind="task", name="news_digest", input={"a": 1})
    store.start(run)
    got = store.get("abc123")
    assert got is not None
    assert got.name == "news_digest"
    assert got.input == {"a": 1}
    assert got.status == STATUS_RUNNING

    got.status = STATUS_OK
    got.output = "done"
    got.steps = [{"index": 0, "action": "x"}]
    got.finished_at = 123.0
    store.update(got)

    got2 = store.get("abc123")
    assert got2.status == STATUS_OK
    assert got2.output == "done"
    assert got2.steps == [{"index": 0, "action": "x"}]
    store.close()


def test_run_store_list(tmp_path):
    store = RunStore(str(tmp_path / "warden.db"))
    store.start(RunRecord(id="a", kind="chat", name="react"))
    store.start(RunRecord(id="b", kind="task", name="repo_report"))
    store.start(RunRecord(id="c", kind="task", name="weekly_review"))
    assert [r.id for r in store.list()] == ["c", "b", "a"]  # 按 started_at 倒序
    assert [r.id for r in store.list(kind="task")] == ["c", "b"]
    store.close()


def test_run_store_schema_idempotent(tmp_path):
    path = str(tmp_path / "warden.db")
    RunStore(path).close()
    RunStore(path).close()  # 二次打开不报错（CREATE IF NOT EXISTS）


def test_trace_span_sets_and_resets():
    assert get_trace_id() == ""
    tid = new_trace_id()
    assert len(tid) == 32
    with trace_span(tid) as cur:
        assert cur == tid
        assert get_trace_id() == tid
    assert get_trace_id() == ""


def test_trace_span_auto_generates():
    with trace_span() as tid:
        assert get_trace_id() == tid
        assert tid  # 非空
