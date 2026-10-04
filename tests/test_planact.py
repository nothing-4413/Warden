"""用假 LLM 验证 PlanAct 循环逻辑。"""
from app.agent.planact import PlanActAgent
from app.config import Settings
from app.tools import build_default_registry


class FakeLLM:
    model = "fake"

    def __init__(self, script):
        self.script = list(script)

    def chat(self, messages, temperature=None):
        return self.script.pop(0)


def test_planact_plans_and_summarizes():
    llm = FakeLLM([
        '{"plan": [{"step": "compute", "tool": "calculator", "args": {"expression": "3 * 4"}}]}',
        "the result is 12",
    ])
    agent = PlanActAgent(Settings(), llm, build_default_registry())
    result = agent.run([{"role": "user", "content": "3*4?"}])

    assert result.answer == "the result is 12"
    assert len(result.steps) == 1
    assert result.steps[0].observation == 12
