"""用假 LLM 验证 ReAct 循环逻辑（不发真实网络请求）。"""
from app.agent.react import ReactAgent
from app.config import Settings
from app.tools import build_default_registry


class FakeLLM:
    model = "fake"

    def __init__(self, script):
        self.script = list(script)  # 每次 chat 弹出下一个响应

    def chat(self, messages, temperature=None):
        return self.script.pop(0)


def test_react_calls_tool_then_answers():
    llm = FakeLLM([
        '{"thought": "need to compute", "action": "calculator", "action_input": {"expression": "2 + 2"}}',
        '{"thought": "got 4", "final_answer": "the result is 4"}',
    ])
    agent = ReactAgent(Settings(), llm, build_default_registry())
    result = agent.run([{"role": "user", "content": "2+2?"}])

    assert result.answer == "the result is 4"
    assert len(result.steps) == 2
    assert result.steps[0].action == "calculator"
    assert result.steps[0].observation == 4


def test_react_unknown_tool_is_reported_back():
    llm = FakeLLM([
        '{"thought": "try", "action": "nonexistent", "action_input": {}}',
        '{"thought": "fallback", "final_answer": "cannot do it"}',
    ])
    agent = ReactAgent(Settings(), llm, build_default_registry())
    result = agent.run([{"role": "user", "content": "hi"}])

    assert "unknown tool" in str(result.steps[0].observation)
    assert result.answer == "cannot do it"
