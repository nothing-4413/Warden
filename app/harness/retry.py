"""重试：带指数退避的通用重试包装。"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import TypeVar

log = logging.getLogger(__name__)
T = TypeVar("T")


def with_retry(
    fn: Callable[[], T],
    *,
    attempts: int = 3,
    backoff_s: float = 1.0,
    retriable: Callable[[Exception], bool] | None = None,
    on_attempt: Callable[[int], None] | None = None,
) -> T:
    """重试 fn 最多 attempts 次，指数退避（1s, 2s, 4s ...）。

    - retriable(e) 返回 False 的异常不重试（默认全部重试）
    - on_attempt(n) 在每次尝试前被调用（n 从 1 开始），用于记录尝试次数
    - attempts 次都失败时，把最后一次异常向上抛
    """
    last: Exception | None = None
    for i in range(attempts):
        if on_attempt is not None:
            on_attempt(i + 1)
        try:
            return fn()
        except Exception as exc:
            if retriable is not None and not retriable(exc):
                raise
            last = exc
            if i < attempts - 1:
                delay = backoff_s * (2**i)
                log.warning("retry %d/%d after %.2fs: %s", i + 1, attempts, delay, exc)
                time.sleep(delay)
    assert last is not None
    raise last
