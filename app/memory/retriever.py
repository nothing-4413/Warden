"""Retriever：查询 → 嵌入 → 向量检索 → 返回相关笔记片段。"""
from __future__ import annotations

from .embeddings import EmbeddingClient
from .vector_store import Chunk, VectorStore


class Retriever:
    def __init__(self, embedder: EmbeddingClient, store: VectorStore) -> None:
        self.embedder = embedder
        self.store = store

    def retrieve(self, query: str, k: int = 4) -> list[Chunk]:
        emb = self.embedder.embed([query])[0]
        return self.store.search(emb, k=k)
