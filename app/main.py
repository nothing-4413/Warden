"""FastAPI 入口：M0 只有健康检查 + 一个对话端点。"""
from __future__ import annotations

from fastapi import FastAPI

from .agent.planact import PlanActAgent
from .agent.react import ReactAgent
from .config import get_settings
from .llm import LLMClient
from .schemas import ChatRequest, ChatResponse, StepView
from .tools import build_default_registry

settings = get_settings()
llm = LLMClient(settings)
registry = build_default_registry()
_agents = {
    "react": ReactAgent(settings, llm, registry),
    "planact": PlanActAgent(settings, llm, registry),
}

app = FastAPI(title="Warden", version="0.1.0")


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "model": llm.model, "tools": registry.names()}


@app.post("/api/v1/chat", response_model=ChatResponse)
def chat(req: ChatRequest) -> ChatResponse:
    agent = _agents[req.agent]
    history = [m.model_dump() for m in req.messages]
    result = agent.run(history)
    return ChatResponse(
        answer=result.answer,
        agent=result.agent,
        model=result.model,
        steps=[StepView(index=s.index, thought=s.thought, action=s.action,
                        action_input=s.action_input, observation=s.observation)
               for s in result.steps],
    )
