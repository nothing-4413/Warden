"""命令行快速调试。

用法：
  python -m app.cli chat "12 * 7 + 3 等于多少？" [--agent react|planact]
  python -m app.cli tasks                       # 列出定时任务
  python -m app.cli run <task_name>             # 手动触发一次任务
  python -m app.cli index-notes                 # 索引个人笔记（RAG）
  python -m app.cli search "关键字"             # 语义检索个人笔记
  python -m app.cli mcp-ls                      # 列出 MCP server 工具
  python -m app.cli team "复杂任务"              # 多 Agent 编排
"""
from __future__ import annotations

import argparse

from .agent.orchestrator import Orchestrator
from .agent.planact import PlanActAgent
from .agent.react import ReactAgent
from .config import get_settings
from .harness import configure_logging
from .harness.run_store import RunStore
from .llm import LLMClient
from .memory import NotesIndexer, Retriever, VectorStore
from .memory.embeddings import EmbeddingClient
from .mcp import build_mcp_registry
from .notify import get_notifier
from .scheduler import Services, TaskNotFoundError, run_once
from .tasks import build_default_task_registry
from .tools import build_default_registry


def _build_memory(settings):
    embedder = EmbeddingClient(settings)
    store = VectorStore(settings.memory_db_path)
    return embedder, store, Retriever(embedder, store)


def _chat(query: str, agent_name: str) -> None:
    settings = get_settings()
    llm = LLMClient(settings)
    store = RunStore(settings.db_path)
    _embedder, mem_store, retriever = _build_memory(settings)
    registry = build_default_registry(retriever=retriever, top_k=settings.retrieval_top_k,
                                      min_score=settings.retrieval_min_score)
    agent_cls = ReactAgent if agent_name == "react" else PlanActAgent
    agent = agent_cls(settings, llm, registry, store=store)

    result = agent.run([{"role": "user", "content": query}])
    print(f"[{result.agent}|{result.model}] {result.answer}")
    for s in result.steps:
        label = s.action or "(answer)"
        print(f"  step {s.index}: {s.thought or ''} -> {label} -> {s.observation}")
    store.close()
    mem_store.close()


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


def _index_notes() -> int:
    settings = get_settings()
    if not settings.notes_dir:
        print("未设置 WARDEN_NOTES_DIR，无法索引笔记（在 .env 里配置后重试）。")
        return 1
    embedder, store, _retriever = _build_memory(settings)
    indexer = NotesIndexer(settings, embedder, store)
    n = indexer.index_dir(settings.notes_dir)
    print(f"已索引 {n} 个 chunk 到 {settings.memory_db_path}")
    store.close()
    return 0


def _search_notes(query: str) -> None:
    settings = get_settings()
    _embedder, store, retriever = _build_memory(settings)
    chunks = retriever.retrieve(query, k=settings.retrieval_top_k)
    if not chunks:
        print("没有在个人笔记里找到相关内容。")
    for c in chunks:
        print(f"[{c.doc_id}] (score={c.score:.2f})\n{c.text}\n")
    store.close()


def _mcp_list() -> int:
    settings = get_settings()
    servers = settings.mcp_servers
    if not servers:
        print("未配置 WARDEN_MCP_SERVERS（JSON 数组 [{name, command:[...]}]）。")
        return 1
    registry, clients = build_mcp_registry(servers)
    try:
        for t in registry.all():
            print(f"- {t.name}: {t.description}")
    finally:
        for c in clients:
            c.close()
    return 0


def _team(query: str) -> int:
    settings = get_settings()
    llm = LLMClient(settings)
    store = RunStore(settings.db_path)
    _embedder, mem_store, retriever = _build_memory(settings)

    # researcher：带 search_notes（RAG 记忆）+ 所有 MCP 工具
    researcher_tools = build_default_registry(retriever=retriever, top_k=settings.retrieval_top_k,
                                              min_score=settings.retrieval_min_score)
    mcp_clients: list = []
    if settings.mcp_servers:
        try:
            mcp_registry, mcp_clients = build_mcp_registry(settings.mcp_servers)
            for t in mcp_registry.all():
                researcher_tools.register(t)
        except Exception as exc:
            print(f"[warn] MCP 工具加载失败，跳过：{exc}")

    specialists = {
        "researcher": ReactAgent(settings, llm, researcher_tools, store=store,
                                 name="researcher", description="检索个人笔记/外部工具并回答问题"),
        "writer": ReactAgent(settings, llm, build_default_registry(), store=store,
                             name="writer", description="基于给定信息写摘要/文案，不检索"),
    }
    orchestrator = Orchestrator(specialists, llm)
    try:
        result = orchestrator.run([{"role": "user", "content": query}])
    finally:
        for c in mcp_clients:
            c.close()
        store.close()
        mem_store.close()
    print(f"[orchestrator -> {result.agent}] {result.answer}")
    return 0


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

    sub.add_parser("index-notes", help="索引个人笔记（RAG 记忆）")

    sub.add_parser("mcp-ls", help="列出 MCP server 暴露的工具")

    p_team = sub.add_parser("team", help="多 Agent 编排：路由到专家")
    p_team.add_argument("query", help="user request")

    p_search = sub.add_parser("search", help="语义检索个人笔记")
    p_search.add_argument("query", help="检索关键词/问题")

    args = parser.parse_args(argv)
    if args.command == "chat":
        _chat(args.query, args.agent)
    elif args.command == "tasks":
        _list_tasks()
    elif args.command == "run":
        return _run_task(args.task)
    elif args.command == "index-notes":
        return _index_notes()
    elif args.command == "mcp-ls":
        return _mcp_list()
    elif args.command == "team":
        return _team(args.query)
    elif args.command == "search":
        _search_notes(args.query)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
