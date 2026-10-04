"""VectorStore：SQLite 存向量（JSON float 数组），暴力余弦相似度检索。

设计取舍：个人笔记量级（数千 chunk）用 SQLite + 暴力检索足够，零外部服务；
接口 add/search 留作换 Milvus/pgvector 只改这一处（与 RunStore 同一思路）。
"""
from __future__ import annotations

import json
import math
import sqlite3
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Chunk:
    id: str
    doc_id: str  # 来源笔记文件路径
    text: str
    score: float  # 与查询的余弦相似度（search 结果才有意义）


class VectorStore:
    def __init__(self, db_path: str) -> None:
        self._path = Path(db_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS chunks (
                id TEXT PRIMARY KEY,
                doc_id TEXT NOT NULL,
                text TEXT NOT NULL,
                embedding TEXT NOT NULL
            )
            """
        )
        self._conn.commit()

    def add(self, chunk_id: str, doc_id: str, text: str, embedding: list[float]) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO chunks (id, doc_id, text, embedding) VALUES (?,?,?,?)",
            (chunk_id, doc_id, text, json.dumps(embedding)),
        )
        self._conn.commit()

    def search(self, query_embedding: list[float], k: int = 4) -> list[Chunk]:
        """暴力余弦检索，返回按相似度降序的 top-k。"""
        rows = self._conn.execute("SELECT id, doc_id, text, embedding FROM chunks").fetchall()
        scored = [(_cosine(query_embedding, json.loads(r["embedding"])), r) for r in rows]
        scored.sort(key=lambda t: t[0], reverse=True)
        return [
            Chunk(id=r["id"], doc_id=r["doc_id"], text=r["text"], score=s)
            for s, r in scored[:k]
        ]

    def count(self) -> int:
        return self._conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]

    def clear(self) -> None:
        self._conn.execute("DELETE FROM chunks")
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)
