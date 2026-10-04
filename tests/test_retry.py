"""with_retry 行为：成功 / 重试后成功 / 耗尽抛错 / retriable / on_attempt 计数。"""
from app.harness.retry import with_retry


def test_success_no_retry():
    calls = []
    result = with_retry(lambda: calls.append(1) or "ok", attempts=3, backoff_s=0)
    assert result == "ok"
    assert calls == [1]


def test_retry_then_success():
    state = {"n": 0}

    def flaky():
        state["n"] += 1
        if state["n"] < 3:
            raise RuntimeError("boom")
        return "done"

    assert with_retry(flaky, attempts=3, backoff_s=0) == "done"
    assert state["n"] == 3


def test_exhaust_raises_last():
    def always_fail():
        raise ValueError("last error")

    try:
        with_retry(always_fail, attempts=3, backoff_s=0)
        assert False, "should have raised"
    except ValueError as exc:
        assert str(exc) == "last error"


def test_retriable_false_does_not_retry():
    state = {"n": 0}

    def fail():
        state["n"] += 1
        raise RuntimeError("not retriable")

    try:
        with_retry(fail, attempts=3, backoff_s=0, retriable=lambda e: False)
        assert False, "should have raised"
    except RuntimeError:
        pass
    assert state["n"] == 1


def test_on_attempt_counts_every_try():
    seen = []

    def fail():
        raise RuntimeError("x")

    try:
        with_retry(fail, attempts=3, backoff_s=0, on_attempt=seen.append)
        assert False, "should have raised"
    except RuntimeError:
        pass
    assert seen == [1, 2, 3]
