#!/usr/bin/env python
"""实验：Function Calling 断点续跑时，上下文会不会被重复注入？

用途：验证 ``FunctionCallAgent`` 的 resume 路径。
子命令
  crash   —— 跑一轮，在第 N 次 chat_with_tools 前用 os._exit 硬崩
  dump    —— 直接打印落库记录里 meta["messages"] 的形状（需在子进程内做，避免崩在主进程）
  resume  —— 续跑，并把喂给模型的 messages 序列打印出来

用法::

    python -m scripts.exp_fc_resume --phase crash --run-id X --crash-at 2
    python -m scripts.exp_fc_resume --phase resume --run-id X
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.agent.function_call import FunctionCallAgent  # noqa: E402
from app.config import Settings  # noqa: E402
from app.harness import RunStore  # noqa: E402
from app.llm import LLMClient  # noqa: E402
from app.tools import build_default_registry  # noqa: E402

CRASH_EXIT = 70
DB = "data/exp_fc_resume.db"
PROMPT = "用计算器算 3 * 4，再用计算器算 5 * 6，最后只回答第二个结果。"


class CrashLLM:
    """第 crash_at 次 chat_with_tools 之前硬崩（不跑 finally、不落 _finish_error）。"""

    def __init__(self, inner, crash_at: int) -> None:
        self._inner = inner
        self._crash_at = crash_at
        self.calls = 0

    def __getattr__(self, name):
        return getattr(self._inner, name)

    def chat_with_tools(self, messages, tools):
        self.calls += 1
        if self.calls >= self._crash_at:
            sys.stdout.flush()
            sys.stderr.flush()
            os._exit(CRASH_EXIT)
        return self._inner.chat_with_tools(messages, tools)


class SpyLLM:
    """包一层，记录每次真正发给模型的 messages 快照。"""

    def __init__(self, inner) -> None:
        self._inner = inner
        self.seen: list[list[dict]] = []

    def __getattr__(self, name):
        return getattr(self._inner, name)

    def chat_with_tools(self, messages, tools):
        self.seen.append(json.loads(json.dumps(messages, default=str)))
        return self._inner.chat_with_tools(messages, tools)


def _agent(llm, store):
    return FunctionCallAgent(Settings(), llm, build_default_registry(), store=store)


def phase_crash(args) -> int:
    settings = Settings()
    store = RunStore(args.db)
    llm = CrashLLM(LLMClient(settings), args.crash_at)
    _agent(llm, store).run([{"role": "user", "content": PROMPT}], trace_id=args.run_id)
    print(f"NOT CRASHED (calls={llm.calls})", flush=True)
    return 0


def phase_resume(args) -> int:
    settings = Settings()
    store = RunStore(args.db)
    record = store.get(args.run_id)
    if record is None:
        print("no record", flush=True)
        return 1
    meta_msgs = (record.meta or {}).get("messages") or []
    print(f"落库 steps={len(record.steps)}", flush=True)
    print(f"落库 meta['messages'] = {len(meta_msgs)} 条", flush=True)
    for i, m in enumerate(meta_msgs):
        role = m.get("role")
        extra = ""
        if m.get("tool_calls"):
            extra = " tool_calls=" + ",".join(c["function"]["name"] for c in m["tool_calls"])
        text = str(m.get("content"))
        print(f"  meta[{i}] {role:<9}{extra} content={text[:80]!r}", flush=True)

    llm = SpyLLM(LLMClient(settings))
    result = _agent(llm, store).resume(args.run_id)
    print(f"\nresume answer = {result.answer!r}", flush=True)
    print(f"resume 实际发给模型的第 1 次请求含 {len(llm.seen[0])} 条消息：", flush=True)
    for i, m in enumerate(llm.seen[0]):
        role = m.get("role")
        extra = ""
        if m.get("tool_calls"):
            extra = " tool_calls=" + ",".join(c["function"]["name"] for c in m["tool_calls"])
        tcid = f" tool_call_id={m.get('tool_call_id')}" if m.get("tool_call_id") else ""
        print(f"  sent[{i}] {role:<9}{extra}{tcid} content={str(m.get('content'))[:80]!r}", flush=True)

    # 重复检测：同一条 assistant(带 tool_calls) 或同一份观测出现几次
    tool_msgs = [m for m in llm.seen[0] if m.get("role") == "tool"]
    obs_msgs = [
        m
        for m in llm.seen[0]
        if m.get("role") == "user" and str(m.get("content", "")).startswith("Observation:")
    ]
    print(f"\nrole='tool' 条数 = {len(tool_msgs)}", flush=True)
    print(f"role='user' 且以 'Observation:' 开头的条数 = {len(obs_msgs)}", flush=True)
    store.close()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", choices=["crash", "resume"], required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--crash-at", type=int, default=2)
    parser.add_argument("--db", default=DB)
    args = parser.parse_args()
    return phase_crash(args) if args.phase == "crash" else phase_resume(args)


if __name__ == "__main__":
    raise SystemExit(main())
