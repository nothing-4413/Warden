"""FastAPI 入口：健康检查 + 对话端点 + 调度任务端点。"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException

from .agent.planact import PlanActAgent
from .agent.react import ReactAgent
from .config import get_settings
from .llm import LLMClient
from .notify import get_notifier
from .scheduler import Services, TaskNotFoundError, build_scheduler, run_once
from .schemas import ChatRequest, ChatResponse, StepView, TaskRunResponse, TaskView
from .tasks import build_default_task_registry
from .tools import build_default_registry

settings = get_settings()
llm = LLMClient(settings)
registry = build_default_registry()
_agents = {
    "react": ReactAgent(settings, llm, registry),
    "planact": PlanActAgent(settings, llm, registry),
}

notifier = get_notifier(settings)
_task_registry = build_default_task_registry(settings)
_task_ctx = Services(settings, llm, notifier)


@asynccontextmanager
async def lifespan(_: FastAPI):
    scheduler = build_scheduler(_task_registry, _task_ctx)
    scheduler.start()
    yield
    scheduler.shutdown(wait=False)


app = FastAPI(title="Warden", version="0.2.0", lifespan=lifespan)


@app.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "model": llm.model,
        "tools": registry.names(),
        "tasks": _task_registry.names(),
    }


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


@app.get("/api/v1/tasks", response_model=list[TaskView])
def list_tasks() -> list[TaskView]:
    return [
        TaskView(name=t.name, description=t.description, schedule=t.schedule)
        for t in _task_registry.all()
    ]


@app.post("/api/v1/tasks/{name}/run", response_model=TaskRunResponse)
def run_task(name: str) -> TaskRunResponse:
    try:
        result = run_once(_task_registry, _task_ctx, name)
    except TaskNotFoundError:
        raise HTTPException(status_code=404, detail=f"unknown task: {name}")
    return TaskRunResponse(
        task=result.task, status=result.status, summary=result.summary,
        detail=result.detail, error=result.error,
    )
