"""记忆（RAG）：嵌入 + 向量库 + 分块索引 + 检索（M3）。"""

from .embeddings import EmbeddingClient, EmbeddingError
from .indexer import NotesIndexer, chunk_text
from .retriever import Retriever
from .vector_store import Chunk, VectorStore

__all__ = [
    "Chunk",
    "EmbeddingClient",
    "EmbeddingError",
    "NotesIndexer",
    "Retriever",
    "VectorStore",
    "chunk_text",
]
