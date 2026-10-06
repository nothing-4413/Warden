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
    llm = FakeLLM(
        [
            '{"thought": "need to compute", "action": "calculator", "action_input": {"expression": "2 + 2"}}',
            '{"thought": "got 4", "final_answer": "the result is 4"}',
        ]
    )
    agent = ReactAgent(Settings(), llm, build_default_registry())
    result = agent.run([{"role": "user", "content": "2+2?"}])

    assert result.answer == "the result is 4"
    assert len(result.steps) == 2
    assert result.steps[0].action == "calculator"
    assert result.steps[0].observation == 4


def test_react_unknown_tool_is_reported_back():
    llm = FakeLLM(
        [
            '{"thought": "try", "action": "nonexistent", "action_input": {}}',
            '{"thought": "fallback", "final_answer": "cannot do it"}',
        ]
    )
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
    registry.register(
        Tool(
            name="long",
            description="returns a long string",
            input_model=NoInput,
            func=lambda: "x" * 5000,
        )
    )
    settings = Settings(max_observation_chars=50)
    agent = ReactAgent(settings, LLMClient(settings), registry)
    obs = agent._execute("long", {})
    assert isinstance(obs, str)
    assert obs.startswith("x" * 50)
    assert len(obs) < 100
    assert "truncated" in obs


def test_react_self_corrects_invalid_json():
    """第一轮输出非法 JSON 时，Agent 自纠重试一次并恢复。"""
    llm = FakeLLM(
        [
            "sorry, I cannot produce JSON",
            '{"thought": "ok", "final_answer": "recovered"}',
        ]
    )
    agent = ReactAgent(Settings(), llm, build_default_registry())
    result = agent.run([{"role": "user", "content": "hi"}])
    assert result.answer == "recovered"
    assert len(result.steps) == 1


def test_react_invalid_json_twice_returns_raw():
    """自纠后仍解析不出 JSON：把原始文本当最终回答，避免死循环。"""
    llm = FakeLLM(["garbage one", "garbage two"])
    agent = ReactAgent(Settings(), llm, build_default_registry())
    result = agent.run([{"role": "user", "content": "hi"}])
    assert result.answer == "garbage two"
    assert len(result.steps) == 1


def test_react_max_steps_forces_final_answer():
    """达到最大步数时强制收尾：让模型基于观测给出最终回答，而非只报诊断。"""
    llm = FakeLLM(
        [
            '{"thought": "a", "action": "calculator", "action_input": {"expression": "1 + 1"}}',
            '{"thought": "b", "action": "calculator", "action_input": {"expression": "2 + 2"}}',
            "the final answer is 6",
        ]
    )
    agent = ReactAgent(Settings(agent_max_steps=2), llm, build_default_registry())
    result = agent.run([{"role": "user", "content": "compute"}])
    assert result.answer == "the final answer is 6"
    assert len(result.steps) == 2


def test_react_system_prompt_includes_fewshot_example():
    """system prompt 注入静态 few-shot 轨迹，提升本地小模型对 JSON 契约的遵从度。"""
    agent = ReactAgent(Settings(), FakeLLM([]), build_default_registry())
    prompt = agent._system_prompt()
    assert "12 * 34" in prompt
    assert '"action": "calculator"' in prompt
    assert "final_answer" in prompt


def test_extract_json_takes_first_object_when_model_emits_two():
    """小模型一轮吐两个对象（action + final_answer）时，取第一个，工具才会被调用。"""
    from app.agent.base import extract_json

    raw = (
        '{"thought": "算一下", "action": "calculator", "action_input": {"expression": "3 * 4"}}\n'
        '{"thought": "算完了", "final_answer": "12"}'
    )
    parsed = extract_json(raw)
    assert parsed["action"] == "calculator"
    assert parsed["action_input"] == {"expression": "3 * 4"}
    assert "final_answer" not in parsed


def test_react_executes_tool_when_two_objects_in_one_turn():
    """一轮里先 action 再 final_answer：仍要先跑工具，再走下一轮。"""
    llm = FakeLLM(
        [
            '{"thought": "算一下", "action": "calculator", "action_input": {"expression": "3 * 4"}}'
            '\n{"thought": "已经知道答案了", "final_answer": "12"}',
            '{"thought": "拿到观测", "final_answer": "12"}',
        ]
    )
    agent = ReactAgent(Settings(), llm, build_default_registry())
    result = agent.run([{"role": "user", "content": "用计算器算 3 * 4，只回答数字。"}])

    assert result.steps[0].action == "calculator"
    assert result.steps[0].observation == 12
    assert result.answer == "12"
    assert len(result.steps) == 2


def test_extract_json_still_rejects_pure_garbage():
    """真没有 JSON 时依旧抛错，让上层走自纠 / 原文兜底。"""
    import pytest

    from app.agent.base import extract_json

    with pytest.raises(ValueError):
        extract_json("抱歉，我无法输出 JSON")
