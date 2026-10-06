"""断点续跑：run 崩溃后 resume 从中断处继续并正确持久化。"""

import pytest

from app.agent.planact import PlanActAgent
from app.agent.react import ReactAgent
from app.config import Settings
from app.harness import STATUS_ERROR, STATUS_OK
from app.harness.run_store import RunStore
from app.tools import build_default_registry


@pytest.fixture
def store(tmp_path):
    """每个用例一个库，跑完关掉（否则测试会话结束时会报未关闭连接的 ResourceWarning）。"""
    s = RunStore(str(tmp_path / "w.db"))
    yield s
    s.close()


class ScriptLLM:
    """按脚本逐条返回；遇到 Exception 项则抛出（模拟 LLM 调用失败 / 运行中断）。"""

    model = "script"

    def __init__(self, script):
        self.script = list(script)
        self.i = 0

    def chat(self, messages, temperature=None):
        item = self.script[self.i]
        self.i += 1
        if isinstance(item, Exception):
            raise item
        return item


def _settings() -> Settings:
    # 重试关到 1 次 + 退避 0，避免真实 sleep；脚本异常立刻向上抛
    return Settings(retry_attempts=1, retry_backoff_s=0)


def test_react_resume_after_tool_step(store):
    tools = build_default_registry()
    s = _settings()

    # run：先成功调 calculator，第二次 chat 抛异常崩溃
    llm1 = ScriptLLM(
        [
            '{"thought": "do it", "action": "calculator", "action_input": {"expression": "2+2"}}',
            RuntimeError("boom"),
        ]
    )
    a1 = ReactAgent(s, llm1, tools, store=store)
    try:
        a1.run([{"role": "user", "content": "2+2?"}])
        raise AssertionError("should have raised")
    except RuntimeError:
        pass

    runs = store.list(kind="chat")
    assert len(runs) == 1
    run_id = runs[0].id
    assert runs[0].status == STATUS_ERROR
    assert len(runs[0].steps) == 1  # 已完成的一步已落库

    # resume：从断点继续，直接给 final_answer
    llm2 = ScriptLLM(['{"thought": "done", "final_answer": "4"}'])
    a2 = ReactAgent(s, llm2, tools, store=store)
    result = a2.resume(run_id)
    assert result.answer == "4"
    assert len(result.steps) == 2  # 原 1 步 + 新 1 步
    assert store.get(run_id).status == STATUS_OK


def test_planact_resume_skips_done_steps(store):
    tools = build_default_registry()
    s = _settings()

    # run：plan 1 个工具步；summarize 阶段抛异常
    llm1 = ScriptLLM(
        [
            '{"plan": [{"step": "compute", "tool": "calculator", "args": {"expression": "3*4"}}]}',
            RuntimeError("summarize failed"),
        ]
    )
    a1 = PlanActAgent(s, llm1, tools, store=store)
    try:
        a1.run([{"role": "user", "content": "3*4?"}])
        raise AssertionError("should have raised")
    except RuntimeError:
        pass

    runs = store.list(kind="chat")
    run_id = runs[0].id
    assert len(runs[0].steps) == 1  # 工具步已执行并落库
    assert runs[0].meta["plan"]  # plan 已存，resume 无需重规划

    # resume：跳过已执行步，直接 summarize
    llm2 = ScriptLLM(["the result is 12"])
    a2 = PlanActAgent(s, llm2, tools, store=store)
    result = a2.resume(run_id)
    assert result.answer == "the result is 12"
    assert len(result.steps) == 1  # 未重复执行工具步
    assert store.get(run_id).status == STATUS_OK


def test_react_run_persists_ok(store):
    llm = ScriptLLM(['{"thought": "d", "final_answer": "hi"}'])
    agent = ReactAgent(_settings(), llm, build_default_registry(), store=store)
    result = agent.run([{"role": "user", "content": "hi"}])
    assert result.answer == "hi"
    rec = store.list(kind="chat")[0]
    assert rec.status == STATUS_OK
    assert rec.output == "hi"
