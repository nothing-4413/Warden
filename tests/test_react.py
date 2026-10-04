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


def test_observation_is_truncated():
    """长工具输出被截断，防止撑爆上下文窗口（上下文工程）。"""
    from pydantic import BaseModel

    from app.llm import LLMClient
    from app.tools import Tool, ToolRegistry

    class NoInput(BaseModel):
        pass

    registry = ToolRegistry()
    registry.register(Tool(name="long", description="returns a long string",
                           input_model=NoInput, func=lambda: "x" * 5000))
    settings = Settings(max_observation_chars=50)
    agent = ReactAgent(settings, LLMClient(settings), registry)
    obs = agent._execute("long", {})
    assert isinstance(obs, str)
    assert obs.startswith("x" * 50)
    assert len(obs) < 100
    assert "truncated" in obs
