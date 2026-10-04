"""多轮上下文窗口（滑动窗口 + 摘要压缩）测试。"""
from app.agent.react import ReactAgent
from app.config import Settings
from app.tools import build_default_registry


class FakeLLM:
    model = "fake"

    def __init__(self, script):
        self.script = list(script)

    def chat(self, messages, temperature=None):
        return self.script.pop(0)


def test_history_within_limit_unchanged():
    """历史未超限：原样返回，不调用 LLM 摘要。"""
    llm = FakeLLM([])  # 不应被消耗
    agent = ReactAgent(Settings(context_max_messages=10), llm, build_default_registry())
    history = [{"role": "user", "content": "hi"}]
    assert agent._history_with_summary(history) == history


def test_history_over_limit_is_summarized():
    """历史超限：保留最近 N 条原文，更早的压成一段摘要放在开头。"""
    llm = FakeLLM(["the summary text"])
    agent = ReactAgent(Settings(context_max_messages=2), llm, build_default_registry())
    history = [
        {"role": "user", "content": "a"},
        {"role": "assistant", "content": "b"},
        {"role": "user", "content": "c"},
    ]
    out = agent._history_with_summary(history)
    assert len(out) == 3  # 摘要 + 最近 2 条
    assert out[0]["role"] == "user"
    assert out[0]["content"].startswith("[Earlier conversation summary]")
    assert "the summary text" in out[0]["content"]
    assert out[1] == {"role": "assistant", "content": "b"}
    assert out[2] == {"role": "user", "content": "c"}


def test_summary_failure_degrades_to_window():
    """摘要 LLM 调用失败：退化为纯窗口（只保留最近 N 条），不阻塞对话。"""

    class BoomLLM:
        model = "fake"

        def chat(self, messages, temperature=None):
            raise RuntimeError("llm down")

    agent = ReactAgent(Settings(context_max_messages=2), BoomLLM(), build_default_registry())
    history = [
        {"role": "user", "content": "a"},
        {"role": "assistant", "content": "b"},
        {"role": "user", "content": "c"},
    ]
    assert agent._history_with_summary(history) == [history[1], history[2]]


def test_run_compresses_long_history_before_loop():
    """run() 入口集成：超限历史先被摘要压缩（消耗 1 次 LLM 调用），再进入循环。"""
    llm = FakeLLM([
        "conversation summary",
        '{"thought": "t", "final_answer": "ok"}',
    ])
    agent = ReactAgent(Settings(context_max_messages=2), llm, build_default_registry())
    history = [
        {"role": "user", "content": "a"},
        {"role": "assistant", "content": "b"},
        {"role": "user", "content": "c"},
    ]
    result = agent.run(history)
    assert result.answer == "ok"
