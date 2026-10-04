"""blackboard_write / blackboard_read：多 Agent 共享黑板的工作记忆工具。

黑板是一个会话内的共享键值存储（Blackboard，见 app/agent/blackboard.py）。
写方 Agent 把中间结果写入黑板，读方 Agent 按 key 读取，实现解耦协作。
注意：黑板是进程内、易失的（与长期记忆 save_note 不同——save_note 入库可跨会话，
黑板只在一次多 Agent 会话内共享）。
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from ..base import Tool


class BlackboardWriteInput(BaseModel):
    key: str = Field(description="黑板的键（简短，如 'findings'）")
    value: str = Field(description="要写入的内容")


class BlackboardReadInput(BaseModel):
    key: str = Field(description="要读取的黑板键")


def make_blackboard_write_tool(blackboard: Any) -> Tool:
    def _write(key: str, value: str) -> str:
        blackboard.write(key, value)
        return f"written to blackboard[{key!r}]"

    return Tool(
        name="blackboard_write",
        description="Write an intermediate result to the shared blackboard (multi-agent "
                    "working memory). Use it to hand off findings/decisions to other agents "
                    "in this session. Pick a short, descriptive key.",
        input_model=BlackboardWriteInput,
        func=_write,
    )


def make_blackboard_read_tool(blackboard: Any) -> Tool:
    def _read(key: str) -> str:
        value = blackboard.read(key)
        return f"blackboard[{key!r}] is empty" if value is None else value

    return Tool(
        name="blackboard_read",
        description="Read a value previously written to the shared blackboard by another "
                    "agent. Use it to retrieve intermediate results handed off to you.",
        input_model=BlackboardReadInput,
        func=_read,
    )
