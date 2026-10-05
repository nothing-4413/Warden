"""save_note：长期记忆"写"侧（读 + 写闭环）测试。"""

from app.config import Settings
from app.memory.indexer import NotesIndexer
from app.memory.retriever import Retriever
from app.memory.vector_store import VectorStore
from app.tools import build_default_registry


class FakeEmbedder:
    """用词袋 hash 生成确定性向量（零网络）。"""

    def embed(self, texts):
        out = []
        for t in texts:
            v = [0.0] * 8
            for w in t.lower().split():
                v[hash(w) % 8] += 1.0
            out.append(v)
        return out


def test_index_text_writes_chunks(tmp_path):
    settings = Settings()
    embedder = FakeEmbedder()
    store = VectorStore(str(tmp_path / "mem.db"))
    indexer = NotesIndexer(settings, embedder, store)

    n = indexer.index_text("hello world", "note/test")
    assert n == 1
    assert store.count() == 1
    store.close()


def test_save_note_tool_registered_with_indexer(tmp_path):
    settings = Settings()
    embedder = FakeEmbedder()
    store = VectorStore(str(tmp_path / "mem.db"))
    indexer = NotesIndexer(settings, embedder, store)

    registry = build_default_registry(indexer=indexer)
    assert "save_note" in registry.names()
    store.close()


def test_save_note_not_registered_without_indexer():
    registry = build_default_registry()
    assert "save_note" not in registry.names()


def test_save_then_search_closed_loop(tmp_path):
    """save_note 写入后，search_notes 能检索到：长期记忆读 + 写闭环。"""
    settings = Settings()
    embedder = FakeEmbedder()
    store = VectorStore(str(tmp_path / "mem.db"))
    indexer = NotesIndexer(settings, embedder, store)
    retriever = Retriever(embedder, store)
    registry = build_default_registry(retriever=retriever, indexer=indexer)

    save_result = registry.get("save_note").run(
        {"content": "the warden project uses a self-built react loop", "topic": "architecture"}
    )
    assert "记住" in save_result

    search_result = registry.get("search_notes").run({"query": "react loop"})
    assert "self-built react loop" in search_result
    store.close()
