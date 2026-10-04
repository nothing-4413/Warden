"""Reflexion-lite（自反思）测试：final answer 前自我校验，必要时回补。"""
from app.agent.react import ReactAgent
from app.config import Settings
from app.tools import build_default_registry


class FakeLLM:
    model = "fake"

    def __init__(self, script):
        self.script = list(script)

    def chat(self, messages, temperature=None):
        return self.script.pop(0)


def test_reflect_ok_returns_answer():
    llm = FakeLLM([
        '{"thought": "t", "final_answer": "the answer"}',
        '{"verdict": "ok", "feedback": ""}',
    ])
    agent = ReactAgent(Settings(reflect_enabled=True), llm, build_default_registry())
    result = agent.run([{"role": "user", "content": "q"}])
    assert result.answer == "the answer"
    assert len(result.steps) == 1  # 反思通过：只留答案步，不额外记录反思步


def test_reflect_redo_refines_answer():
    llm = FakeLLM([
        '{"thought": "t", "final_answer": "wrong"}',
        '{"verdict": "redo", "feedback": "include the actual number"}',
        '{"thought": "fixed", "final_answer": "corrected"}',
        '{"verdict": "ok", "feedback": ""}',
    ])
    agent = ReactAgent(Settings(reflect_enabled=True), llm, build_default_registry())
    result = agent.run([{"role": "user", "content": "q"}])
    assert result.answer == "corrected"
    reflect_steps = [s for s in result.steps if s.action == "_reflect"]
    assert len(reflect_steps) == 1
    assert "actual number" in reflect_steps[0].observation


def test_reflect_parse_failure_passes_through():
    """反思输出解析失败：默认放行，不阻塞主循环。"""
    llm = FakeLLM([
        '{"thought": "t", "final_answer": "answer"}',
        "garbage one",
        "garbage two",
    ])
    agent = ReactAgent(Settings(reflect_enabled=True), llm, build_default_registry())
    result = agent.run([{"role": "user", "content": "q"}])
    assert result.answer == "answer"
    assert len(result.steps) == 1


def test_reflect_disabled_by_default_uses_one_call():
    """默认关闭自反思：final answer 直接返回，不额外调 LLM。"""
    llm = FakeLLM(['{"thought": "t", "final_answer": "answer"}'])
    agent = ReactAgent(Settings(), llm, build_default_registry())
    result = agent.run([{"role": "user", "content": "q"}])
    assert result.answer == "answer"
    assert llm.script == []  # 恰好一次 LLM 调用，无反思调用
