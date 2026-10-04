"""命令行快速调试：python -m app.cli "12 * 7 + 3 等于多少？" [--agent react]"""
from __future__ import annotations

import argparse

from .agent.planact import PlanActAgent
from .agent.react import ReactAgent
from .config import get_settings
from .llm import LLMClient
from .tools import build_default_registry


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="warden", description="Warden CLI")
    parser.add_argument("query", help="user request")
    parser.add_argument("--agent", choices=["react", "planact"], default="react")
    args = parser.parse_args(argv)

    settings = get_settings()
    llm = LLMClient(settings)
    registry = build_default_registry()
    agent_cls = ReactAgent if args.agent == "react" else PlanActAgent
    agent = agent_cls(settings, llm, registry)

    result = agent.run([{"role": "user", "content": args.query}])
    print(f"[{result.agent}|{result.model}] {result.answer}")
    for s in result.steps:
        label = s.action or "(answer)"
        print(f"  step {s.index}: {s.thought or ''} -> {label} -> {s.observation}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
