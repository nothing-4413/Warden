"""Retriever：查询 → 嵌入 → 向量检索 → 返回相关笔记片段。"""
from __future__ import annotations

from .embeddings import EmbeddingClient
from .vector_store import Chunk, VectorStore


class Retriever:
    def __init__(self, embedder: EmbeddingClient, store: VectorStore) -> None:
        self.embedder = embedder
        self.store = store

    def retrieve(self, query: str, k: int = 4, min_score: float = 0.0) -> list[Chunk]:
        emb = self.embedder.embed([query])[0]
        chunks = self.store.search(emb, k=k)
        # 阈值过滤：低相似度片段会稀释上下文，直接丢弃（上下文工程）
        return [c for c in chunks if c.score >= min_score]
