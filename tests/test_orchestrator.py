"""M4 Part2：多 Agent 编排器（路由 + 专家委派）。"""
from app.agent.orchestrator import Orchestrator
from app.agent.react import ReactAgent
from app.config import Settings
from app.tools import build_default_registry


class FakeLLM:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    @property
    def model(self):
        return "fake"

    def chat(self, messages):
        self.calls.append(messages)
        return self.responses.pop(0)


def _settings():
    return Settings(retry_attempts=1, retry_backoff_s=0)


def _specialist(name, description, answer):
    llm = FakeLLM([f'{{"thought": "ok", "final_answer": "{answer}"}}'])
    agent = ReactAgent(_settings(), llm, build_default_registry(),
                       name=name, description=description)
    return agent, llm


def test_orchestrator_routes_to_correct_specialist():
    researcher, r_llm = _specialist("researcher", "检索并回答问题", "答案是 42")
    writer, w_llm = _specialist("writer", "写文案", "文案")
    router = FakeLLM(['{"specialist": "researcher", "task": "帮我查一下"}'])

    orch = Orchestrator({"researcher": researcher, "writer": writer}, router)
    result = orch.run([{"role": "user", "content": "查一下答案"}])

    assert result.agent == "researcher"
    assert result.answer == "答案是 42"
    # 只有被选中的专家被调用
    assert len(r_llm.calls) == 1
    assert len(w_llm.calls) == 0


def test_orchestrator_unknown_specialist_falls_back_to_first():
    specialist, llm = _specialist("writer", "写文案", "兜底")
    router = FakeLLM(['{"specialist": "nope", "task": "x"}'])
    orch = Orchestrator({"writer": specialist}, router)
    result = orch.run([{"role": "user", "content": "x"}])
    assert result.agent == "writer"
    assert result.answer == "兜底"


def test_specialist_custom_name_overrides_class_attr():
    specialist, _ = _specialist("researcher", "检索", "ok")
    assert specialist.name == "researcher"
    assert specialist.description == "检索"
