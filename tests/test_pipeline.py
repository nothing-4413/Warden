"""CriticPipeline：多 Agent 接力（researcher → critic），用假 LLM 验证接力顺序。"""

from app.agent.pipeline import CriticPipeline
from app.agent.react import ReactAgent
from app.config import Settings
from app.tools import ToolRegistry, build_default_registry


class FakeLLM:
    model = "fake"

    def __init__(self, script):
        self.script = list(script)

    def chat(self, messages, temperature=None):
        return self.script.pop(0)


def test_critic_pipeline_researcher_then_critic():
    settings = Settings()
    researcher = ReactAgent(
        settings,
        FakeLLM(
            [
                '{"thought": "search", "final_answer": "findings: the answer is 42"}',
            ]
        ),
        build_default_registry(),
        name="researcher",
    )
    critic = ReactAgent(
        settings,
        FakeLLM(
            [
                '{"thought": "review", "final_answer": "improved: 42, but with caveats"}',
            ]
        ),
        ToolRegistry(),
        name="critic",
    )

    pipeline = CriticPipeline(researcher, critic)
    result = pipeline.run("what is the answer?")

    assert result.answer == "improved: 42, but with caveats"
    assert result.agent == "critic_pipeline"
    # 步骤合并了 researcher 与 critic 的 steps（各 1 步）
    assert len(result.steps) == 2


def test_critic_pipeline_critic_sees_findings():
    seen = {}

    class SpyLLM(FakeLLM):
        def chat(self, messages, temperature=None):
            seen["critic_input"] = messages[-1]["content"]
            return super().chat(messages, temperature)

    settings = Settings()
    researcher = ReactAgent(
        settings,
        FakeLLM(
            [
                '{"final_answer": "the sky is blue"}',
            ]
        ),
        build_default_registry(),
        name="researcher",
    )
    critic = ReactAgent(
        settings,
        SpyLLM(
            [
                '{"final_answer": "ok"}',
            ]
        ),
        ToolRegistry(),
        name="critic",
    )

    CriticPipeline(researcher, critic).run("what color is the sky?")

    assert "the sky is blue" in seen["critic_input"]
    assert "what color is the sky?" in seen["critic_input"]
