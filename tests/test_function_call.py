"""原生 Function Calling 循环：用假 LLM 验证工具调用/回填/最终回答（零网络）。"""
from app.agent.function_call import FunctionCallAgent
from app.config import Settings
from app.llm import ToolCall
from app.tools import build_default_registry


class FakeToolLLM:
    """chat_with_tools 返回 (content, [ToolCall])；chat 返回纯文本。"""

    model = "fake"

    def __init__(self, script):
        self.script = list(script)

    def chat_with_tools(self, messages, tools):
        return self.script.pop(0)

    def chat(self, messages, temperature=None):
        return self.script.pop(0)


def test_function_call_runs_tool_then_answers():
    llm = FakeToolLLM([
        ("", [ToolCall(id="c1", name="calculator", arguments={"expression": "2 + 2"})]),
        ("the result is 4", []),
    ])
    agent = FunctionCallAgent(Settings(), llm, build_default_registry())
    result = agent.run([{"role": "user", "content": "2+2?"}])
    assert result.answer == "the result is 4"
    assert result.steps[0].action == "calculator"
    assert result.steps[0].observation == 4


def test_function_call_unknown_tool_reports_error_and_recovers():
    llm = FakeToolLLM([
        ("", [ToolCall(id="c1", name="nope", arguments={})]),
        ("cannot do it", []),
    ])
    agent = FunctionCallAgent(Settings(), llm, build_default_registry())
    result = agent.run([{"role": "user", "content": "hi"}])
    assert "unknown tool" in str(result.steps[0].observation)
    assert result.answer == "cannot do it"


def test_function_call_sends_openai_tool_spec():
    captured = {}

    class CaptureLLM:
        model = "fake"

        def chat_with_tools(self, messages, tools):
            captured["tools"] = tools
            return ("ok", [])

        def chat(self, messages, temperature=None):
            return "ok"

    agent = FunctionCallAgent(Settings(), CaptureLLM(), build_default_registry())
    agent.run([{"role": "user", "content": "hi"}])
    names = [t["function"]["name"] for t in captured["tools"]]
    assert "calculator" in names and "get_current_time" in names
    assert captured["tools"][0]["type"] == "function"
    assert "parameters" in captured["tools"][0]["function"]


def test_function_call_system_prompt_includes_fewshot_hint():
    """system prompt 注入工具选择的 few-shot 提示。"""
    agent = FunctionCallAgent(Settings(), FakeToolLLM([]), build_default_registry())
    prompt = agent._system_prompt()
    assert "12 * 34" in prompt
    assert "calculator" in prompt
