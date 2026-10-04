"""FastAPI 入口：健康检查 + 对话端点 + 调度任务端点（含 trace_id + 幂等回放）。"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Response

from .agent.planact import PlanActAgent
from .agent.react import ReactAgent
from .config import get_settings
from .harness import STATUS_OK, configure_logging, metrics_text, trace_span
from .harness.run_store import RunStore
from .llm import LLMClient
from .notify import get_notifier
from .scheduler import Services, TaskNotFoundError, build_scheduler, run_once
from .schemas import ChatRequest, ChatResponse, StepView, TaskRunResponse, TaskView
from .tasks import build_default_task_registry
from .tools import build_default_registry

configure_logging()

settings = get_settings()
llm = LLMClient(settings)
registry = build_default_registry()
store = RunStore(settings.db_path)
_agents = {
    "react": ReactAgent(settings, llm, registry, store=store),
    "planact": PlanActAgent(settings, llm, registry, store=store),
}

notifier = get_notifier(settings)
_task_registry = build_default_task_registry(settings)
_task_ctx = Services(settings, llm, notifier, store=store)


@asynccontextmanager
async def lifespan(_: FastAPI):
    scheduler = build_scheduler(_task_registry, _task_ctx)
    scheduler.start()
    yield
    scheduler.shutdown(wait=False)


app = FastAPI(title="Warden", version="0.3.0", lifespan=lifespan)


@app.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "model": llm.model,
        "tools": registry.names(),
        "tasks": _task_registry.names(),
    }


@app.get("/metrics")
def metrics() -> Response:
    """Prometheus 文本格式指标（Grafana 数据源 / 失败率告警的数据源）。"""
    if not settings.metrics_enabled:
        raise HTTPException(status_code=404, detail="metrics disabled")
    return Response(content=metrics_text(), media_type="text/plain; version=0.0.4; charset=utf-8")


@app.post("/api/v1/chat", response_model=ChatResponse)
def chat(req: ChatRequest) -> ChatResponse:
    with trace_span(req.request_id) as trace_id:
        # 幂等回放：同 request_id 已成功运行过 → 直接返回缓存答案，不再调 LLM
        if req.request_id:
            existing = store.get(req.request_id)
            if existing and existing.status == STATUS_OK and existing.output:
                return ChatResponse(
                    answer=existing.output, agent=req.agent, model=llm.model, steps=[]
                )

        agent = _agents[req.agent]
        history = [m.model_dump() for m in req.messages]
        result = agent.run(history, trace_id=trace_id)
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
