#!/usr/bin/env python
"""崩溃恢复评测：跨进程硬崩（os._exit）后 resume，统计恢复成功率。

崩溃点固定在「工具已经执行并落库、正要进入下一轮模型调用之前」：此时上下文里
已经出现 ``Observation: ...``（见 ``app/agent/react.py`` 的 ``_loop``），说明至少
有一个 ``AgentStep`` 已经写进 SQLite。用 ``os._exit`` 直接杀进程 —— 不跑 finally、
不落 ``_finish_error``，留下的就是 ``running`` 记录，跟真实断电/被 kill 等价。

于是评测回答的是：**半成品落库后进程硬崩，另起进程 resume 能不能接着跑完、
会不会重复执行已完成的步骤。**

用法::

    python -m scripts.eval_resume --trials 24
    python -m scripts.eval_resume --trials 5 --tasks a --out data/eval/resume.json

需要能跑通 JSON 协议（``{"thought","action","action_input"}``）的对话模型。
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.agent.react import ReactAgent  # noqa: E402
from app.config import Settings  # noqa: E402
from app.harness import RunStore  # noqa: E402
from app.llm import LLMClient  # noqa: E402
from app.tools import build_default_registry  # noqa: E402

# (任务名, 提示词, 期望答案)
TASKS: dict[str, tuple[str, str]] = {
    "a": ("用计算器算 3 * 4，只回答数字。", "12"),
    "b": ("先用计算器算 12 * 12，再把上一步的结果加上 100，最后只回答数字。", "244"),
    "c": (
        "先用 get_current_time 查一次当前时间，再用计算器算 9 * 9，最后只回答那个乘法结果。",
        "81",
    ),
}
# 崩溃点 = 「已落库的 Observation 条数达到 N 时，在下一次 LLM 调用前杀掉进程」
# 同一任务给多个点，是为了覆盖「崩在第 1 步之后」与「崩在链条中间」两种半成品状态。
CRASH_POINTS: dict[str, list[int]] = {"a": [1], "b": [1, 2], "c": [1, 2]}
CRASH_EXIT = 70
MARKER = "EVAL_RESULT "


class CrashLLM:
    """上下文里已带 N 条 Observation 时，在下一次 chat 之前硬杀进程。

    其余调用与属性全部委托给真实客户端。
    """

    def __init__(self, inner: LLMClient, crash_at: int) -> None:
        self._inner = inner
        self._crash_at = crash_at
        self.calls = 0

    def __getattr__(self, name: str):
        return getattr(self._inner, name)

    @staticmethod
    def _observations(messages: list[dict]) -> int:
        return sum(1 for m in messages if str(m.get("content", "")).startswith("Observation:"))

    def chat(self, messages: list[dict], temperature: float | None = None) -> str:
        self.calls += 1
        if self._observations(messages) >= self._crash_at:
            sys.stdout.flush()
            sys.stderr.flush()
            os._exit(CRASH_EXIT)
        return self._inner.chat(messages, temperature=temperature)


def build_agent(settings: Settings, llm, db_path: str) -> tuple[ReactAgent, RunStore]:
    store = RunStore(db_path)
    return ReactAgent(settings, llm, build_default_registry(), store=store), store


def phase_crash(args: argparse.Namespace) -> int:
    """子进程：跑到第 crash_at 次 LLM 调用前自杀。"""
    settings = Settings()
    llm = CrashLLM(LLMClient(settings), args.crash_at)
    agent, _store = build_agent(settings, llm, args.db)
    prompt, _expected = TASKS[args.task]
    agent.run([{"role": "user", "content": prompt}], trace_id=args.run_id)
    # 没崩就说明崩溃点没被触发（模型没走那么多步）
    print(MARKER + json.dumps({"crashed": False, "calls": llm.calls}, ensure_ascii=False))
    return 0


def phase_resume(args: argparse.Namespace) -> int:
    """子进程：读库里残留的 running 记录续跑，把结果打到 stdout。"""
    settings = Settings()
    agent, store = build_agent(settings, LLMClient(settings), args.db)
    try:
        result = agent.resume(args.run_id)
        record = store.get(args.run_id)
        payload = {
            "status": record.status if record else "missing",
            "answer": result.answer,
            "steps": [s.to_dict() for s in result.steps],
        }
        print(MARKER + json.dumps(payload, ensure_ascii=False))
        return 0
    except Exception as exc:
        record = store.get(args.run_id)
        print(
            MARKER
            + json.dumps(
                {
                    "status": record.status if record else "missing",
                    "error": f"{type(exc).__name__}: {exc}",
                },
                ensure_ascii=False,
            )
        )
        return 1


def run_child(argv: list[str], timeout: int) -> tuple[int, str, str]:
    proc = subprocess.run(
        [sys.executable, __file__, *argv],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )
    return proc.returncode, proc.stdout or "", proc.stderr or ""


def parse_payload(stdout: str) -> dict:
    for line in reversed(stdout.splitlines()):
        if line.startswith(MARKER):
            return json.loads(line[len(MARKER) :])
    return {}


def duplicate_steps(steps: list[dict]) -> int:
    seen: set[str] = set()
    dupes = 0
    for step in steps:
        key = json.dumps(
            [step.get("action"), step.get("action_input")], sort_keys=True, ensure_ascii=False
        )
        if step.get("action") is None:
            continue
        if key in seen:
            dupes += 1
        seen.add(key)
    return dupes


def orchestrate(args: argparse.Namespace) -> int:
    db = Path(args.db)
    if args.reset and db.exists():
        db.unlink()
    db.parent.mkdir(parents=True, exist_ok=True)

    combos: list[tuple[str, int]] = []
    for name in args.tasks:
        for point in CRASH_POINTS[name]:
            combos.append((name, point))
    if not combos:
        print("没有可用的 (任务, 崩溃点) 组合", file=sys.stderr)
        return 2

    trials: list[dict] = []
    for index in range(args.trials):
        task, crash_at = combos[index % len(combos)]
        run_id = f"eval-{uuid.uuid4().hex[:12]}"
        prompt, expected = TASKS[task]
        entry: dict = {"run_id": run_id, "task": task, "crash_at": crash_at, "prompt": prompt}
        print(f"[{index + 1}/{args.trials}] {task} crash_at={crash_at} {run_id}", flush=True)

        code, _out, err = run_child(
            [
                "--phase",
                "crash",
                "--run-id",
                run_id,
                "--task",
                task,
                "--crash-at",
                str(crash_at),
                "--db",
                args.db,
            ],
            args.timeout,
        )
        entry["crash_exit"] = code
        if code != CRASH_EXIT:
            entry["outcome"] = "no_crash"
            entry["stderr_tail"] = err[-400:]
            trials.append(entry)
            print("    → 未触发崩溃（进程正常结束），从分母剔除")
            continue

        store = RunStore(args.db)
        record = store.get(run_id)
        entry["steps_before_resume"] = len(record.steps) if record else -1
        entry["status_before_resume"] = record.status if record else "missing"
        store.close()

        if entry["steps_before_resume"] < 1:
            # 崩溃发生在任何步骤落库之前 —— 不算「半成品续跑」，剔除
            entry["outcome"] = "no_partial_state"
            trials.append(entry)
            print("    → 崩溃时还没有步骤落库，从分母剔除")
            continue

        code, out, err = run_child(
            ["--phase", "resume", "--run-id", run_id, "--db", args.db],
            args.timeout,
        )
        payload = parse_payload(out)
        entry["resume_exit"] = code
        entry["status_after_resume"] = payload.get("status", "unknown")
        answer = str(payload.get("answer", ""))
        entry["answer"] = answer
        entry["steps_after_resume"] = len(payload.get("steps") or [])
        entry["duplicate_steps"] = duplicate_steps(payload.get("steps") or [])
        if "error" in payload:
            entry["error"] = payload["error"]
        ok = (
            code == 0
            and entry["status_after_resume"] == "ok"
            and expected in answer
            and entry["duplicate_steps"] == 0
        )
        entry["outcome"] = "resumed_ok" if ok else "resume_failed"
        if not ok:
            entry["stderr_tail"] = err[-400:]
        trials.append(entry)
        print(
            f"    → {entry['outcome']} status={entry['status_after_resume']} "
            f"answer={answer!r} steps {entry['steps_before_resume']}→{entry['steps_after_resume']}"
        )

    crashed = [t for t in trials if t["outcome"] not in ("no_crash", "no_partial_state")]
    ok = [t for t in crashed if t["outcome"] == "resumed_ok"]
    rate = (len(ok) / len(crashed)) if crashed else 0.0
    skipped = [t for t in trials if t["outcome"] in ("no_crash", "no_partial_state")]
    report = {
        "model": Settings().llm_model,
        "endpoint": Settings().llm_base_url,
        "trials": len(trials),
        "crashed": len(crashed),
        "skipped": len(skipped),
        "no_crash": sum(1 for t in skipped if t["outcome"] == "no_crash"),
        "no_partial_state": sum(1 for t in skipped if t["outcome"] == "no_partial_state"),
        "resumed_ok": len(ok),
        "resume_success_rate": rate,
        "steps_before_resume": sorted({t["steps_before_resume"] for t in crashed}),
        "details": trials,
    }
    print(
        f"\n崩溃（半成品已落库）{len(crashed)} 次（另有 {len(skipped)} 次未触发/无落库，已剔除），"
        f"恢复成功 {len(ok)} 次 → 恢复成功率 {rate:.1%}"
    )
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"结果已写入 {out}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Warden 崩溃恢复评测（os._exit + resume）")
    parser.add_argument("--phase", choices=["crash", "resume"], help="子进程阶段（内部使用）")
    parser.add_argument("--run-id", default="", help="子进程阶段用 run id")
    parser.add_argument("--task", choices=sorted(TASKS), default="a")
    parser.add_argument(
        "--crash-at", type=int, default=1, help="已落库的 Observation 数达到该值时崩溃"
    )
    parser.add_argument("--db", default="data/eval_resume.db")
    parser.add_argument("--trials", type=int, default=24)
    parser.add_argument("--tasks", default="a,b", help="参与轮转的任务，逗号分隔")
    parser.add_argument("--timeout", type=int, default=600, help="单个子进程超时（秒）")
    parser.add_argument("--reset", action="store_true", default=True)
    parser.add_argument("--keep-db", dest="reset", action="store_false")
    parser.add_argument("--out", default="data/eval/resume.json")
    args = parser.parse_args()
    args.tasks = [t.strip() for t in args.tasks.split(",") if t.strip()]

    if args.phase == "crash":
        return phase_crash(args)
    if args.phase == "resume":
        return phase_resume(args)
    return orchestrate(args)


if __name__ == "__main__":
    raise SystemExit(main())
