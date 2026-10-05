"""Blackboard：多 Agent 共享工作记忆（黑板），用假 LLM 验证写→读协作。"""

from app.agent.blackboard import Blackboard, BlackboardTeam
from app.agent.react import ReactAgent
from app.config import Settings
from app.tools import ToolRegistry
from app.tools.builtin.blackboard import make_blackboard_read_tool, make_blackboard_write_tool


class FakeLLM:
    model = "fake"

    def __init__(self, script):
        self.script = list(script)

    def chat(self, messages, temperature=None):
        return self.script.pop(0)


def test_blackboard_write_read_snapshot():
    bb = Blackboard()
    bb.write("a", "1")
    assert bb.read("a") == "1"
    assert bb.read("missing") is None
    assert bb.read("missing", "d") == "d"
    assert bb.keys() == ["a"]
    assert bb.snapshot() == {"a": "1"}


def test_blackboard_tools_roundtrip():
    bb = Blackboard()
    registry = ToolRegistry()
    registry.register(make_blackboard_write_tool(bb))
    registry.register(make_blackboard_read_tool(bb))
    registry.get("blackboard_write").run({"key": "findings", "value": "42"})
    assert bb.read("findings") == "42"
    assert registry.get("blackboard_read").run({"key": "findings"}) == "42"
    assert "empty" in registry.get("blackboard_read").run({"key": "nope"})


def test_blackboard_team_researcher_writes_writer_reads():
    settings = Settings()
    bb = Blackboard()

    researcher_tools = ToolRegistry()
    researcher_tools.register(make_blackboard_write_tool(bb))
    researcher = ReactAgent(
        settings,
        FakeLLM(
            [
                '{"thought": "research", "action": "blackboard_write", '
                '"action_input": {"key": "findings", "value": "the answer is 42"}}',
                '{"thought": "handoff", "final_answer": "findings recorded"}',
            ]
        ),
        researcher_tools,
        name="researcher",
    )

    writer_tools = ToolRegistry()
    writer_tools.register(make_blackboard_read_tool(bb))
    writer = ReactAgent(
        settings,
        FakeLLM(
            [
                '{"thought": "read", "action": "blackboard_read", '
                '"action_input": {"key": "findings"}}',
                '{"thought": "synthesize", "final_answer": "the answer is 42"}',
            ]
        ),
        writer_tools,
        name="writer",
    )

    team = BlackboardTeam(bb)
    team.add(researcher, "research and write findings to the blackboard")
    team.add(writer, "read the blackboard and write the final answer")

    result = team.run("what is the answer?")

    assert bb.read("findings") == "the answer is 42"
    assert result.answer == "the answer is 42"
    assert result.agent == "blackboard_team"
    # 两个成员各 2 步（工具调用 + 最终回答），steps 全量合并
    assert len(result.steps) == 4
    # writer 确实读到了 researcher 写入的值（观测里可见）
    assert result.steps[2].action == "blackboard_read"
    assert result.steps[2].observation == "the answer is 42"


def test_blackboard_team_empty_raises():
    team = BlackboardTeam()
    try:
        team.run("x")
    except ValueError as exc:
        assert "at least one member" in str(exc)
    else:
        raise AssertionError("expected ValueError")
