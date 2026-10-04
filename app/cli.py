"""命令行快速调试。

用法：
  python -m app.cli chat "12 * 7 + 3 等于多少？" [--agent react|planact]
  python -m app.cli tasks                  # 列出定时任务
  python -m app.cli run <task_name>        # 手动触发一次任务
"""
from __future__ import annotations

import argparse

from .agent.planact import PlanActAgent
from .agent.react import ReactAgent
from .config import get_settings
from .harness import configure_logging
from .harness.run_store import RunStore
from .llm import LLMClient
from .notify import get_notifier
from .scheduler import Services, TaskNotFoundError, run_once
from .tasks import build_default_task_registry
from .tools import build_default_registry


def _chat(query: str, agent_name: str) -> None:
    settings = get_settings()
    llm = LLMClient(settings)
    store = RunStore(settings.db_path)
    registry = build_default_registry()
    agent_cls = ReactAgent if agent_name == "react" else PlanActAgent
    agent = agent_cls(settings, llm, registry, store=store)

    result = agent.run([{"role": "user", "content": query}])
    print(f"[{result.agent}|{result.model}] {result.answer}")
    for s in result.steps:
        label = s.action or "(answer)"
        print(f"  step {s.index}: {s.thought or ''} -> {label} -> {s.observation}")
    store.close()


def _list_tasks() -> None:
    settings = get_settings()
    reg = build_default_task_registry(settings)
    for t in reg.all():
        print(f"- {t.name}: {t.description}  (schedule={t.schedule})")


def _run_task(name: str) -> int:
    settings = get_settings()
    llm = LLMClient(settings)
    notifier = get_notifier(settings)
    store = RunStore(settings.db_path)
    reg = build_default_task_registry(settings)
    ctx = Services(settings, llm, notifier, store=store)
    try:
        result = run_once(reg, ctx, name)
    except TaskNotFoundError as exc:
        print(f"错误：{exc}")
        return 1
    print(f"[{result.task}] {result.status}: {result.summary}")
    store.close()
    return 0 if result.status != "error" else 1


def main(argv: list[str] | None = None) -> int:
    configure_logging()
    parser = argparse.ArgumentParser(prog="warden", description="Warden CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    p_chat = sub.add_parser("chat", help="对话式 Agent")
    p_chat.add_argument("query", help="user request")
    p_chat.add_argument("--agent", choices=["react", "planact"], default="react")

    sub.add_parser("tasks", help="列出定时任务")

    p_run = sub.add_parser("run", help="手动触发一次任务")
    p_run.add_argument("task", help="task name")

    args = parser.parse_args(argv)
    if args.command == "chat":
        _chat(args.query, args.agent)
    elif args.command == "tasks":
        _list_tasks()
    elif args.command == "run":
        return _run_task(args.task)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
