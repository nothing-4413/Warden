"""trace_id 全链路追踪：contextvar + logging 注入。"""
from __future__ import annotations

import contextlib
import contextvars
import logging
import uuid

_trace_var: contextvars.ContextVar[str] = contextvars.ContextVar("warden_trace_id", default="")

_LOG_FORMAT = "%(asctime)s %(levelname)s [%(trace_id)s] %(name)s: %(message)s"


def new_trace_id() -> str:
    return uuid.uuid4().hex


def get_trace_id() -> str:
    """当前上下文（含子线程/异步任务继承）里的 trace_id，无则空串。"""
    return _trace_var.get()


@contextlib.contextmanager
def trace_span(trace_id: str | None = None):
    """进入一段带 trace_id 的上下文；不传则新生成。子线程/子协程自动继承。"""
    token = _trace_var.set(trace_id or new_trace_id())
    try:
        yield _trace_var.get()
    finally:
        _trace_var.reset(token)


class TraceFilter(logging.Filter):
    """给每条日志记录注入 trace_id 字段。"""

    def filter(self, record: logging.LogRecord) -> bool:
        record.trace_id = _trace_var.get() or "-"
        return True


def configure_logging(level: int = logging.INFO) -> None:
    """装配根 logger：统一格式 + trace_id。main/cli 入口各调用一次。"""
    handler = logging.StreamHandler()
    handler.addFilter(TraceFilter())
    handler.setFormatter(logging.Formatter(_LOG_FORMAT))
    root = logging.getLogger()
    root.setLevel(level)
    root.handlers[:] = [handler]
