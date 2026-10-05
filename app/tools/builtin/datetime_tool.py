"""示例工具：返回当前本地时间，演示无参数工具。"""

from __future__ import annotations

from datetime import UTC, datetime

from pydantic import BaseModel

from ..base import Tool


class NowInput(BaseModel):
    """无参数：LLM 调用时传空对象 {}。"""


def _now() -> str:
    return datetime.now(UTC).astimezone().isoformat(timespec="seconds")


datetime_tool = Tool(
    name="get_current_time",
    description="Get the current local date and time.",
    input_model=NowInput,
    func=_now,
)
