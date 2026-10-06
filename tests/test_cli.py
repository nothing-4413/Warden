"""CLI 入口：子命令分发、输出与退出码。

LLM / 嵌入都换成本地假实现（零网络）；多 Agent 的三个子命令只替换编排器，
各编排器自身的逻辑由 test_orchestrator / test_pipeline / test_blackboard 覆盖。

注意 `monkeypatch.chdir(tmp_path)`：`Settings` 会读工作目录下的 `.env`，
换到 tmp 目录可保证测试只看到本文件显式设置的环境变量。
"""

from __future__ import annotations

import json
from collections.abc import Iterator

import pytest

from app import cli
from app.config import Settings


class FakeEmbedder:
    """确定性向量：同一文本永远同一向量，所以自己索引的内容自己必然命中。"""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [[0.25, 0.5, 0.75] for _ in texts]


class ScriptLLM:
    """按脚本逐条返回；脚本用完就回一个正常的 final_answer。"""

    model = "script"

    def __init__(self, script: list[str] | None = None) -> None:
        self.script = list(script or [])
        self.i = 0

    def chat(self, messages, temperature=None):
        if self.i < len(self.script):
            item = self.script[self.i]
            self.i += 1
            return item
        return '{"thought": "done", "final_answer": "42"}'

    def chat_with_tools(self, messages, tools):
        """Function Calling 循环用：不带工具调用，直接给答案。"""
        return ("4", [])


class _Step:
    def __init__(self, index: int, action: str | None, thought: str, observation) -> None:
        self.index, self.action = index, action
        self.thought, self.observation = thought, observation


class _Result:
    agent, model, answer = "react", "script", "4"

    def __init__(self, steps=(), self_eval=None) -> None:
        self.steps = list(steps)
        self.self_eval = self_eval


class _TaskResult:
    def __init__(self, task: str = "news_digest", status: str = "ok", summary: str = "3 条新资讯"):
        self.task, self.status, self.summary = task, status, summary


@pytest.fixture
def cli_env(tmp_path, monkeypatch) -> Iterator[type[ScriptLLM]]:
    monkeypatch.chdir(tmp_path)  # 别读到仓库根目录的 .env
    monkeypatch.setenv("WARDEN_DB_PATH", str(tmp_path / "warden.db"))
    monkeypatch.setenv("WARDEN_MEMORY_DB_PATH", str(tmp_path / "memory.db"))
    monkeypatch.setenv("WARDEN_REPORT_DIR", str(tmp_path / "reports"))
    monkeypatch.setenv("WARDEN_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("WARDEN_NOTES_DIR", "")
    monkeypatch.setenv("WARDEN_MCP_SERVERS", "")
    monkeypatch.setenv("WARDEN_RETRY_ATTEMPTS", "1")
    monkeypatch.setenv("WARDEN_RETRY_BACKOFF_S", "0")
    monkeypatch.setattr("app.config._settings", None)  # get_settings 的单例缓存
    monkeypatch.setattr(cli, "EmbeddingClient", FakeEmbedder)
    monkeypatch.setattr(
        cli, "LLMClient", lambda settings: ScriptLLM(['{"thought": "do it", "final_answer": "4"}'])
    )
    yield ScriptLLM


# ---------- tasks / run ----------


def test_tasks_lists_default_tasks(cli_env, capsys) -> None:
    assert cli.main(["tasks"]) == 0
    out = capsys.readouterr().out
    for name in ("news_digest", "repo_report", "weekly_review"):
        assert name in out


def test_run_reports_task_not_found(cli_env, monkeypatch, capsys) -> None:
    def boom(registry, ctx, name):
        raise cli.TaskNotFoundError(f"没有名为 {name} 的任务")

    monkeypatch.setattr(cli, "run_once", boom)
    assert cli.main(["run", "nope"]) == 1
    assert "没有名为 nope 的任务" in capsys.readouterr().out


def test_run_ok_and_error_exit_codes(cli_env, monkeypatch, capsys) -> None:
    monkeypatch.setattr(cli, "run_once", lambda *a, **k: _TaskResult())
    assert cli.main(["run", "news_digest"]) == 0
    assert "[news_digest] ok: 3 条新资讯" in capsys.readouterr().out

    monkeypatch.setattr(
        cli, "run_once", lambda *a, **k: _TaskResult(status="error", summary="炸了")
    )
    assert cli.main(["run", "news_digest"]) == 1
    assert "error: 炸了" in capsys.readouterr().out


# ---------- chat ----------


def test_chat_prints_answer_and_steps(cli_env, capsys) -> None:
    assert cli.main(["chat", "2+2 等于几？"]) == 0
    assert "[react|script] 4" in capsys.readouterr().out


def test_chat_prints_steps_and_self_eval(cli_env, monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        cli,
        "ReactAgent",
        lambda *a, **k: type(
            "A",
            (),
            {
                "run": lambda self, msgs: _Result(
                    steps=[_Step(0, "calculator", "算一下", 4)],
                    self_eval={"confidence": 0.9, "reason": "工具确认过"},
                )
            },
        )(),
    )
    assert cli.main(["chat", "2+2", "--agent", "react"]) == 0
    out = capsys.readouterr().out
    assert "step 0: 算一下 -> calculator -> 4" in out
    assert "[self-eval] confidence=0.9  工具确认过" in out


def test_chat_supports_planact(cli_env, monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        cli,
        "LLMClient",
        lambda settings: ScriptLLM(
            [
                '{"plan": [{"step": "compute", "tool": "calculator", '
                '"args": {"expression": "2+2"}}]}',
                "答案是 4",
            ]
        ),
    )
    assert cli.main(["chat", "2+2", "--agent", "planact"]) == 0
    assert "[planact|script] 答案是 4" in capsys.readouterr().out


def test_chat_supports_function_call(cli_env, capsys) -> None:
    assert cli.main(["chat", "2+2", "--agent", "function_call"]) == 0
    assert "[function_call|script] 4" in capsys.readouterr().out


def test_chat_rejects_unknown_agent(cli_env) -> None:
    with pytest.raises(SystemExit):
        cli.main(["chat", "hi", "--agent", "bogus"])


def test_requires_a_subcommand(cli_env) -> None:
    with pytest.raises(SystemExit):
        cli.main([])


# ---------- 记忆 ----------


def test_index_notes_without_notes_dir(cli_env, capsys) -> None:
    assert cli.main(["index-notes"]) == 1
    assert "未设置 WARDEN_NOTES_DIR" in capsys.readouterr().out


def test_index_then_search_notes(cli_env, tmp_path, monkeypatch, capsys) -> None:
    notes = tmp_path / "notes"
    notes.mkdir()
    (notes / "a.md").write_text("Warden 是一个自托管的多智能体系统，" * 10, encoding="utf-8")
    monkeypatch.setenv("WARDEN_NOTES_DIR", str(notes))
    monkeypatch.setattr("app.config._settings", None)

    assert cli.main(["index-notes"]) == 0
    assert "已索引" in capsys.readouterr().out

    assert cli.main(["search", "多智能体"]) == 0
    out = capsys.readouterr().out
    assert "a.md" in out and "score=" in out


def test_search_notes_without_hits(cli_env, capsys) -> None:
    assert cli.main(["search", "还没有索引过"]) == 0
    assert "没有在个人笔记里找到相关内容" in capsys.readouterr().out


# ---------- MCP ----------


def test_mcp_list_without_servers(cli_env, capsys) -> None:
    assert cli.main(["mcp-ls"]) == 1
    assert "未配置 WARDEN_MCP_SERVERS" in capsys.readouterr().out


def test_mcp_list_prints_tools_and_closes_clients(cli_env, monkeypatch, capsys) -> None:
    monkeypatch.setenv("WARDEN_MCP_SERVERS", json.dumps([{"name": "fs", "command": ["x"]}]))
    monkeypatch.setattr("app.config._settings", None)
    closed: list[str] = []

    class _Tool:
        def __init__(self, name: str) -> None:
            self.name, self.description = name, f"{name} 工具"

    class _Registry:
        def all(self):
            return [_Tool("mcp_read"), _Tool("mcp_write")]

    class _Client:
        def close(self):
            closed.append("client")

    monkeypatch.setattr(cli, "build_mcp_registry", lambda servers: (_Registry(), [_Client()]))

    assert cli.main(["mcp-ls"]) == 0
    out = capsys.readouterr().out
    assert "- mcp_read: mcp_read 工具" in out and "- mcp_write" in out
    assert closed == ["client"]  # 即使打印成功也要关掉子进程


# ---------- 多 Agent（编排器换成桩，只测 cli 的接线） ----------


class _StubTeam:
    """Orchestrator / CriticPipeline / BlackboardTeam 共用的桩。"""

    def __init__(self, *args, **kwargs) -> None:
        self.args, self.kwargs = args, kwargs

    def add(self, *args, **kwargs) -> None:
        pass

    def run(self, query) -> _Result:
        return _Result()


class _StubOrchestrator(_StubTeam):
    def run(self, query) -> _Result:
        result = _Result()
        result.agent = "researcher"
        return result


class _StubPipeline(_StubTeam):
    def run(self, query) -> _Result:
        result = _Result()
        result.agent = "critic"
        return result


class _StubBlackboard(_StubTeam):
    def run(self, query) -> _Result:
        result = _Result(steps=[_Step(0, None, "写完了", "最终答案")])
        result.agent = "writer"
        return result


def test_team_runs_orchestrator(cli_env, monkeypatch, capsys) -> None:
    monkeypatch.setattr(cli, "Orchestrator", _StubOrchestrator)
    assert cli.main(["team", "帮我查点东西"]) == 0
    assert "[orchestrator -> researcher] 4" in capsys.readouterr().out


def test_research_runs_pipeline(cli_env, monkeypatch, capsys) -> None:
    monkeypatch.setattr(cli, "CriticPipeline", _StubPipeline)
    assert cli.main(["research", "一个复杂问题"]) == 0
    assert "[critic] 4" in capsys.readouterr().out


def test_collab_runs_blackboard_team(cli_env, monkeypatch, capsys) -> None:
    monkeypatch.setattr(cli, "BlackboardTeam", _StubBlackboard)
    assert cli.main(["collab", "一起写点东西"]) == 0
    out = capsys.readouterr().out
    assert "[writer] 4" in out
    assert "step 0: 写完了 -> (answer) -> 最终答案" in out


def test_team_survives_broken_mcp_server(cli_env, monkeypatch, capsys) -> None:
    """MCP 配错时只警告、不影响编排（真实场景里 MCP server 经常起不来）。"""
    monkeypatch.setenv("WARDEN_MCP_SERVERS", json.dumps([{"name": "fs", "command": ["nope"]}]))
    monkeypatch.setattr("app.config._settings", None)

    def boom(servers):
        raise RuntimeError("cannot spawn")

    monkeypatch.setattr(cli, "build_mcp_registry", boom)
    monkeypatch.setattr(cli, "Orchestrator", _StubOrchestrator)
    assert cli.main(["team", "任务"]) == 0
    out = capsys.readouterr().out
    assert "[warn] MCP 工具加载失败" in out and "cannot spawn" in out
