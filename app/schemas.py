"""API 请求/响应模型。"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class ChatMessage(BaseModel):
    role: Literal["system", "user", "assistant"]
    content: str


class ChatRequest(BaseModel):
    messages: list[ChatMessage] = Field(min_length=1)
    agent: Literal["react", "planact"] = "react"
    request_id: str | None = None  # 幂等键：同 id 重复请求直接回放已存结果


class StepView(BaseModel):
    index: int
    thought: str | None = None
    action: str | None = None
    action_input: dict[str, Any] | None = None
    observation: Any = None


class ChatResponse(BaseModel):
    answer: str
    agent: str
    model: str
    steps: list[StepView]


class TaskView(BaseModel):
    name: str
    description: str
    schedule: dict[str, Any]


class TaskRunResponse(BaseModel):
    task: str
    status: str
    summary: str
    detail: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
