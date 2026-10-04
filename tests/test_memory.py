"""M3 记忆：分块 + 向量库增查 + 检索器 + search_notes 工具。"""
from app.memory.indexer import chunk_text
from app.memory.retriever import Retriever
from app.memory.vector_store import VectorStore
from app.tools import build_default_registry


class FakeEmbedder:
    """用词袋 hash 生成确定性向量：相似文本得到更接近的向量（零网络）。"""

    def embed(self, texts):
        out = []
        for t in texts:
            v = [0.0] * 8
            for w in t.lower().split():
                v[hash(w) % 8] += 1.0
            out.append(v)
        return out


def test_chunk_text_splits_and_merges():
    text = "第一段。" * 50 + "\n\n" + "第二段。" * 50
    chunks = chunk_text(text, chunk_size=200, overlap=20)
    assert len(chunks) >= 2
    assert all(len(c) <= 200 for c in chunks)


def test_chunk_text_empty():
    assert chunk_text("   ") == []


def test_vector_store_add_and_search(tmp_path):
    store = VectorStore(str(tmp_path / "mem.db"))
    store.add("c1", "a.md", "apple banana", [1.0, 0.0, 0.0])
    store.add("c2", "b.md", "cherry pie", [0.0, 1.0, 0.0])
    store.add("c3", "c.md", "apple pie", [1.0, 0.0, 1.0])
    results = store.search([1.0, 0.0, 0.0], k=2)
    assert results[0].id == "c1"
    assert results[0].doc_id == "a.md"
    assert results[0].score > results[1].score
    assert store.count() == 3
    store.close()


def test_retriever_returns_top_chunk(tmp_path):
    store = VectorStore(str(tmp_path / "mem.db"))
    embedder = FakeEmbedder()
    store.add("a", "n1.md", "apple banana", embedder.embed(["apple banana"])[0])
    store.add("b", "n2.md", "cherry pie", embedder.embed(["cherry pie"])[0])
    retriever = Retriever(embedder, store)
    chunks = retriever.retrieve("apple", k=1)
    assert chunks[0].id == "a"
    store.close()


def test_search_notes_tool_registered(tmp_path):
    store = VectorStore(str(tmp_path / "mem.db"))
    embedder = FakeEmbedder()
    retriever = Retriever(embedder, store)
    store.add("a", "n.md", "warden is a personal agent framework",
              embedder.embed(["warden agent framework"])[0])
    registry = build_default_registry(retriever=retriever, top_k=2)
    assert "search_notes" in registry.names()
    result = registry.get("search_notes").run({"query": "warden"})
    assert "warden" in result.lower()
    store.close()


def test_default_registry_has_no_search_notes_without_retriever():
    registry = build_default_registry()
    assert "search_notes" not in registry.names()
    assert registry.names() == ["calculator", "get_current_time"]


def test_retriever_min_score_filters_low_similarity(tmp_path):
    store = VectorStore(str(tmp_path / "mem.db"))

    class FixedEmbedder:
        """查询恒返回 [1,0]，与向量 [1,0] 余弦=1、与 [0,1] 余弦=0，结果确定。"""

        def embed(self, texts):
            return [[1.0, 0.0] for _ in texts]

    store.add("a", "n1.md", "match", [1.0, 0.0])
    store.add("b", "n2.md", "orthogonal", [0.0, 1.0])
    retriever = Retriever(FixedEmbedder(), store)
    chunks = retriever.retrieve("anything", k=5, min_score=0.5)
    assert [c.id for c in chunks] == ["a"]
    store.close()
