"""端到端冒烟：用真实 LLM 跑通全部 Agent 模式，验证整条链路（非离线单测）。

覆盖：LLM 探活、react / planact / function_call 三种循环、research 接力、team 路由。
用法（需 LLM 在线，默认 http://localhost:11434/v1，模型见 .env）：
  python -m scripts.smoke

退出码：0 = 全部通过；1 = 至少一项失败（或 LLM 不可达）。
"""
from __future__ import annotations

import sys
from pathlib import Path

# 兼容 `python scripts/smoke.py`（脚本目录在 sys.path，而非仓库根）
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.agent.function_call import FunctionCallAgent  # noqa: E402
from app.agent.orchestrator import Orchestrator  # noqa: E402
from app.agent.pipeline import CriticPipeline  # noqa: E402
from app.agent.planact import PlanActAgent  # noqa: E402
from app.agent.react import ReactAgent  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.harness.run_store import RunStore  # noqa: E402
from app.llm import LLMClient  # noqa: E402
from app.memory import Retriever, VectorStore  # noqa: E402
from app.memory.embeddings import EmbeddingClient  # noqa: E402
from app.tools import ToolRegistry, build_default_registry  # noqa: E402


def _check(name: str, fn) -> bool:
    try:
        answer = fn()
    except Exception as exc:
        print(f"[FAIL] {name}: {type(exc).__name__}: {exc}")
        return False
    text = (answer or "").strip()
    if not text:
        print(f"[FAIL] {name}: empty answer")
        return False
    print(f"[ ok ] {name}: {text[:80]}")
    return True


def main() -> int:
    settings = get_settings()
    print(f"LLM: {settings.llm_base_url}  model={settings.llm_model}")

    llm = LLMClient(settings)
    store = RunStore(settings.db_path)

    # 1. 探活：一次最小 chat
    try:
        probe = llm.chat([{"role": "user", "content": "Reply with the single word: ok"}])
    except Exception as exc:
        print(f"[FAIL] LLM 不可达：{type(exc).__name__}: {exc}")
        return 1
    print(f"[ ok ] LLM reachable (probe={probe.strip()[:40]!r})")

    # 2. 记忆（retriever 非 None 才注册 search_notes；未索引笔记时检索为空，不影响冒烟）
    embedder = EmbeddingClient(settings)
    mem_store = VectorStore(settings.memory_db_path)
    retriever = Retriever(embedder, mem_store)
    registry = build_default_registry(retriever=retriever, top_k=settings.retrieval_top_k,
                                      min_score=settings.retrieval_min_score)

    # 3. 三种自研循环（各自用计算器工具验证真实工具调用）
    react = ReactAgent(settings, llm, registry, store=store)
    planact = PlanActAgent(settings, llm, registry, store=store)
    fc = FunctionCallAgent(settings, llm, registry, store=store)

    results = [
        _check("react", lambda: react.run(
            [{"role": "user", "content": "用计算器算 3 * 4，只回答数字。"}]).answer),
        _check("planact", lambda: planact.run(
            [{"role": "user", "content": "用计算器算 12 / 4，只回答数字。"}]).answer),
        _check("function_call", lambda: fc.run(
            [{"role": "user", "content": "用计算器算 7 + 8，只回答数字。"}]).answer),
    ]

    # 4. 多 Agent：researcher（带工具）→ critic（挑错补缺）接力
    researcher = ReactAgent(settings, llm, registry, store=store, name="researcher",
                            description="检索并查证事实")
    critic = ReactAgent(settings, llm, ToolRegistry(), store=store, name="critic",
                        description="挑错补缺并改写最终答案")
    results.append(_check("research", lambda: CriticPipeline(researcher, critic).run(
        "2 + 2 等于几？").answer))

    # 5. 多 Agent：team 路由
    writer = ReactAgent(settings, llm, ToolRegistry(), store=store, name="writer",
                        description="纯生成")
    results.append(_check("team", lambda: Orchestrator(
        {"researcher": researcher, "writer": writer}, llm).run(
        [{"role": "user", "content": "帮我算 5 * 6 等于几？"}]).answer))

    store.close()
    mem_store.close()

    print("\n" + ("ALL PASS" if all(results) else "SOME FAILED"))
    return 0 if all(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
