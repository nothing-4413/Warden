"""NotesIndexer：把个人笔记目录索引成向量（分块 → 嵌入 → 入库）。"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

from ..config import Settings
from .embeddings import EmbeddingClient
from .vector_store import VectorStore

_PARAGRAPH_SPLIT = re.compile(r"\n\s*\n")


def chunk_text(text: str, chunk_size: int = 600, overlap: int = 100) -> list[str]:
    """把一篇笔记切成语义尽量完整的 chunk。

    策略：先按空行切段落，贪心合并到 chunk_size；单段超长则硬切（带 overlap）。
    """
    if chunk_size <= 0:
        chunk_size = 600
    overlap = max(0, min(overlap, chunk_size - 1))
    text = text.strip()
    if not text:
        return []
    paragraphs = [p.strip() for p in _PARAGRAPH_SPLIT.split(text) if p.strip()]
    chunks: list[str] = []
    current = ""
    for p in paragraphs:
        if not current:
            current = p
        elif len(current) + 2 + len(p) <= chunk_size:
            current += "\n\n" + p
        else:
            _append_hard(chunks, current, chunk_size, overlap)
            current = p
    if current:
        _append_hard(chunks, current, chunk_size, overlap)
    return chunks


def _append_hard(chunks: list[str], text: str, chunk_size: int, overlap: int) -> None:
    while len(text) > chunk_size:
        chunks.append(text[:chunk_size])
        text = text[chunk_size - overlap:] if overlap else text[chunk_size:]
    if text.strip():
        chunks.append(text)


class NotesIndexer:
    def __init__(self, settings: Settings, embedder: EmbeddingClient, store: VectorStore) -> None:
        self.settings = settings
        self.embedder = embedder
        self.store = store

    def index_text(self, text: str, doc_id: str) -> int:
        """把单条文本分块、嵌入、写入向量库，返回写入 chunk 数。

        doc_id 是来源标识：index_dir 用笔记文件路径，save_note 用 note/{topic}/{uuid}。
        chunk id = sha1(f"{doc_id}:{i}")，同一 doc_id 重写幂等（INSERT OR REPLACE）。
        """
        chunks = chunk_text(text, self.settings.chunk_size, self.settings.chunk_overlap)
        if not chunks:
            return 0
        # 一次嵌入整篇文本的所有 chunk（单次 HTTP 请求）
        embeddings = self.embedder.embed(chunks)
        for i, (chunk, emb) in enumerate(zip(chunks, embeddings)):
            cid = hashlib.sha1(f"{doc_id}:{i}".encode("utf-8")).hexdigest()
            self.store.add(cid, doc_id, chunk, emb)
        return len(chunks)

    def index_dir(self, notes_dir: str) -> int:
        """索引目录下所有 *.md，返回新增 chunk 数。"""
        root = Path(notes_dir)
        if not root.is_dir():
            return 0
        added = 0
        for md in sorted(root.rglob("*.md")):
            added += self.index_text(md.read_text(encoding="utf-8"), str(md))
        return added
