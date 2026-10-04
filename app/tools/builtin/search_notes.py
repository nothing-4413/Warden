"""search_notes：语义检索个人笔记（RAG 记忆工具）。

使用前需先 `python -m app.cli index-notes` 建索引。
"""
from __future__ import annotations

from pydantic import BaseModel, Field

from ...memory.retriever import Retriever
from ..base import Tool


class SearchNotesInput(BaseModel):
    query: str = Field(description="要检索的自然语言问题或关键词")


def make_search_notes_tool(retriever: Retriever, top_k: int = 4,
                           min_score: float = 0.0, rewrite: bool = False,
                           rerank: bool = False) -> Tool:
    def _search(query: str) -> str:
        chunks = retriever.retrieve(query, k=top_k, min_score=min_score,
                                    rewrite=rewrite, rerank=rerank)
        if not chunks:
            return "没有在个人笔记里找到相关内容。"
        lines = [f"[{c.doc_id}] (score={c.score:.2f})\n{c.text}" for c in chunks]
        return "\n\n---\n\n".join(lines)

    return Tool(
        name="search_notes",
        description="Search your personal notes (RAG memory). Retrieve relevant note "
                    "fragments by semantic similarity. Use it when the user asks about "
                    "their own notes, past thoughts, or stored knowledge. When you use "
                    "a fragment in your answer, cite its [doc_id].",
        input_model=SearchNotesInput,
        func=_search,
    )
