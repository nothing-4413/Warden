"""Retriever：查询 → 嵌入 → 向量检索 → 返回相关笔记片段。

检索增强（可选，传入 llm 后可用）：① 查询改写（检索前用 LLM 改写/扩展查询提升召回）；
② LLM 重排（检索后对候选片段精排，挑最相关的 top-k 提升精度）。两级管线："召回 + 精排"。
"""

from __future__ import annotations

from .embeddings import EmbeddingClient
from .vector_store import Chunk, VectorStore

_QUERY_REWRITE_PROMPT = """\
Rewrite the user's search query into a more specific, keyword-rich retrieval query for
semantic search over personal notes. Expand abbreviations and add likely synonyms.
Return ONLY the rewritten query, no explanation.
"""

_RERANK_PROMPT = """\
You are ranking note fragments by relevance to a query.
Given the query and the candidates below, return the candidate numbers (1-based) of the
most relevant fragments, ordered from most to least relevant.
Reply with EXACTLY ONE JSON object: {"ranked": [2, 1, 3]}
"""


class Retriever:
    def __init__(self, embedder: EmbeddingClient, store: VectorStore, llm=None) -> None:
        self.embedder = embedder
        self.store = store
        self.llm = llm  # 可选：查询改写 + 重排需要 LLM

    def retrieve(
        self,
        query: str,
        k: int = 4,
        min_score: float = 0.0,
        rewrite: bool = False,
        rerank: bool = False,
    ) -> list[Chunk]:
        # ① 召回：可选用 LLM 改写查询后嵌入；重排时先多取一些候选（k*2）
        search_query = self._rewrite_query(query) if (rewrite and self.llm is not None) else query
        emb = self.embedder.embed([search_query])[0]
        fetch_k = k * 2 if (rerank and self.llm is not None) else k
        chunks = self.store.search(emb, k=fetch_k)
        # 阈值过滤：低相似度片段会稀释上下文，直接丢弃（上下文工程）
        chunks = [c for c in chunks if c.score >= min_score]
        # ② 精排：可选让 LLM 重排候选，挑最相关的 k 条
        if rerank and self.llm is not None and len(chunks) > 1:
            chunks = self._rerank(query, chunks, k)
        return chunks[:k]

    def _rewrite_query(self, query: str) -> str:
        """LLM 改写查询以提升召回；失败则原样返回，不阻塞检索。"""
        try:
            rewritten = self.llm.chat(
                [
                    {"role": "system", "content": _QUERY_REWRITE_PROMPT},
                    {"role": "user", "content": query},
                ]
            ).strip()
            return rewritten or query
        except Exception:
            return query

    def _rerank(self, query: str, chunks: list[Chunk], k: int) -> list[Chunk]:
        """LLM 重排候选片段（按相关性从高到低）；失败则保持原相似度顺序。"""
        options = "\n\n".join(f"[{i}] {c.text}" for i, c in enumerate(chunks, start=1))
        try:
            raw = self.llm.chat(
                [
                    {"role": "system", "content": _RERANK_PROMPT},
                    {"role": "user", "content": f"Query: {query}\n\nCandidates:\n{options}"},
                ]
            )
            # 延迟导入，避免 memory ↔ tools 的模块加载循环
            from ..agent.base import extract_json

            data = extract_json(raw)
            if not isinstance(data, dict):
                return chunks[:k]
            order = data.get("ranked") or []
        except Exception:
            return chunks[:k]

        ranked: list[Chunk] = []
        seen: set[int] = set()
        for idx in order:
            if isinstance(idx, int) and 1 <= idx <= len(chunks) and idx not in seen:
                ranked.append(chunks[idx - 1])
                seen.add(idx)
        # 补齐模型未提及的候选（保持原相似度顺序），再截断到 k
        for i, c in enumerate(chunks, start=1):
            if i not in seen:
                ranked.append(c)
        return ranked[:k]
