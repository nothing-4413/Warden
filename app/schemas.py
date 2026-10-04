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
