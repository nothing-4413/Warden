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
    llm = FakeLLM(
        [
            '{"plan": [{"step": "compute", "tool": "calculator", "args": {"expression": "3 * 4"}}]}',
            "the result is 12",
        ]
    )
    agent = PlanActAgent(Settings(), llm, build_default_registry())
    result = agent.run([{"role": "user", "content": "3*4?"}])

    assert result.answer == "the result is 12"
    assert len(result.steps) == 1
    assert result.steps[0].observation == 12


class SpyLLM:
    """记录每次 chat 调用（用于断言重规划被触发的次数）。"""

    model = "fake"

    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    def chat(self, messages, temperature=None):
        self.calls.append(messages)
        return self.script.pop(0)


def test_planact_replans_after_tool_failure():
    """某步工具失败时，PlanAct 重规划剩余步骤并继续执行。"""
    llm = FakeLLM(
        [
            '{"plan": [{"step": "try bad", "tool": "nope", "args": {}}]}',
            '{"plan": [{"step": "compute", "tool": "calculator", "args": {"expression": "2+2"}}]}',
            "the result is 4",
        ]
    )
    agent = PlanActAgent(Settings(), llm, build_default_registry())
    result = agent.run([{"role": "user", "content": "compute 2+2"}])

    assert result.answer == "the result is 4"
    assert len(result.steps) == 2
    assert result.steps[0].action == "nope"
    assert "unknown tool" in str(result.steps[0].observation)
    assert result.steps[1].action == "calculator"
    assert result.steps[1].observation == 4


def test_planact_replanning_is_bounded():
    """重规划有上限（max_replans），失败步骤不会导致无限重规划。"""
    llm = SpyLLM(
        [
            '{"plan": [{"step": "bad1", "tool": "nope", "args": {}}]}',
            '{"plan": [{"step": "bad2", "tool": "nope", "args": {}}]}',
            "done",
        ]
    )
    agent = PlanActAgent(Settings(max_replans=1), llm, build_default_registry())
    result = agent.run([{"role": "user", "content": "do it"}])

    assert result.answer == "done"
    assert len(result.steps) == 2
    # plan(1) + replan(1) + summarize(1) = 3 次 LLM 调用，证明只重规划了 1 次（有界）
    assert len(llm.calls) == 3
