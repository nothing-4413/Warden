"""置信度自评（self-eval）：验证开启 / 关闭 / 失败 / 提示词四态，不发真实网络。"""

from app.agent.react import ReactAgent
from app.config import Settings
from app.tools import build_default_registry


class FakeLLM:
    model = "fake"

    def __init__(self, script):
        self.script = list(script)  # 每次 chat 弹出下一个响应

    def chat(self, messages, temperature=None):
        return self.script.pop(0)


def test_self_eval_off_by_default():
    llm = FakeLLM(
        [
            '{"thought": "done", "final_answer": "the result is 4"}',
        ]
    )
    agent = ReactAgent(Settings(), llm, build_default_registry())
    result = agent.run([{"role": "user", "content": "2+2?"}])

    assert result.answer == "the result is 4"
    assert result.self_eval is None


def test_self_eval_attaches_confidence_when_enabled():
    llm = FakeLLM(
        [
            '{"thought": "done", "final_answer": "the result is 4"}',
            '{"confidence": 0.9, "reason": "deterministic arithmetic"}',
        ]
    )
    agent = ReactAgent(Settings(self_eval_enabled=True), llm, build_default_registry())
    result = agent.run([{"role": "user", "content": "2+2?"}])

    assert result.self_eval == {"confidence": 0.9, "reason": "deterministic arithmetic"}


def test_self_eval_failure_is_silent():
    """自评失败（LLM 返回垃圾）静默返回 None，绝不影响主流程。"""
    llm = FakeLLM(
        [
            '{"thought": "done", "final_answer": "the result is 4"}',
            "not json at all",
        ]
    )
    agent = ReactAgent(Settings(self_eval_enabled=True), llm, build_default_registry())
    result = agent.run([{"role": "user", "content": "2+2?"}])

    assert result.answer == "the result is 4"
    assert result.self_eval is None


def test_self_eval_prompt_includes_the_answer():
    """自评 prompt 带上模型自己的答案，确认是在评估"自己的产出"。"""
    calls: list = []

    class SpyLLM:
        model = "fake"

        def chat(self, messages, temperature=None):
            calls.append(messages)
            if len(calls) == 1:
                return '{"thought": "done", "final_answer": "answer 42"}'
            return '{"confidence": 1.0, "reason": "sure"}'

    agent = ReactAgent(Settings(self_eval_enabled=True), SpyLLM(), build_default_registry())
    agent.run([{"role": "user", "content": "hi"}])

    assert len(calls) == 2
    assert "answer 42" in calls[1][-1]["content"]
