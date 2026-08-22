"""retrievers 包 — EmptyRetriever / HybridRetriever 测试。"""
import pytest
from langchain_core.documents import Document

from app.rag.retrievers.empty_retriever import EmptyRetriever
from app.rag.retrievers.hybrid_retriever import HybridRetriever
from app.utils.config import chroma_config
from tests.fakes import FakeChromaStore

try:
    from langchain_classic.retrievers import EnsembleRetriever
except ImportError:  # pragma: no cover
    EnsembleRetriever = None


class FakeChromaWithRetriever(FakeChromaStore):
    """FakeChromaStore + as_retriever（HybridRetriever.get_retriever 需要）。"""

    def __init__(self, retriever=None):
        super().__init__()
        self._retriever = retriever or EmptyRetriever()

    def as_retriever(self, search_type=None, search_kwargs=None):
        return self._retriever


def _doc(content, user_id="u1", **meta):
    return Document(page_content=content, metadata={"user_id": user_id, **meta})


class TestEmptyRetriever:
    async def test_ainvoke_returns_empty(self):
        r = EmptyRetriever()
        assert await r.ainvoke("任意查询") == []

    def test_invoke_returns_empty(self):
        r = EmptyRetriever()
        assert r.invoke("任意查询") == []


class TestHybridRetrieverConstruction:
    def test_stores_vectors_store(self):
        store = FakeChromaStore()
        hr = HybridRetriever(store)
        assert hr.vectors_store is store


class TestGetBm25Retriever:
    async def test_no_user_id_returns_none(self):
        hr = HybridRetriever(FakeChromaStore())
        assert await hr.get_bm25_retriever(None) is None

    async def test_returns_bm25_retriever_for_user_docs(self):
        store = FakeChromaStore()
        store.add_documents([_doc("hello world test document", "u1")])
        hr = HybridRetriever(store)

        bm25 = await hr.get_bm25_retriever("u1")
        assert bm25 is not None
        assert bm25.k == chroma_config["k"]
        results = await bm25.ainvoke("hello")
        assert len(results) >= 1
        assert results[0].page_content == "hello world test document"

    async def test_no_docs_returns_none(self):
        hr = HybridRetriever(FakeChromaStore())
        assert await hr.get_bm25_retriever("u1") is None


class TestGetAllDocuments:
    async def test_reads_via_vectors_store_get(self):
        store = FakeChromaStore()
        store.add_documents([
            _doc("content-1", "u1", source="a.txt"),
            _doc("content-2", "u1", source="b.txt"),
        ])
        hr = HybridRetriever(store)
        docs = await hr._get_all_documents()
        assert len(docs) == 2
        assert {d.page_content for d in docs} == {"content-1", "content-2"}
        assert all(d.metadata["user_id"] == "u1" for d in docs)

    async def test_empty_store(self):
        hr = HybridRetriever(FakeChromaStore())
        assert await hr._get_all_documents() == []


class TestGetRetriever:
    async def test_no_user_id_returns_empty_retriever(self):
        hr = HybridRetriever(FakeChromaWithRetriever())
        result = await hr.get_retriever("q")
        assert isinstance(result, EmptyRetriever)

    async def test_empty_store_returns_vector_retriever_only(self):
        fake_retriever = EmptyRetriever()
        hr = HybridRetriever(FakeChromaWithRetriever(retriever=fake_retriever))
        result = await hr.get_retriever("q", "u1")
        # 无 BM25 文档 → 直接返回向量检索器
        assert result is fake_retriever

    async def test_seeded_store_returns_ensemble(self):
        if EnsembleRetriever is None:  # pragma: no cover
            pytest.skip("langchain_classic 未安装")
        store = FakeChromaWithRetriever()
        store.add_documents([_doc("hello world", "u1")])
        hr = HybridRetriever(store)
        result = await hr.get_retriever("q", "u1")
        assert isinstance(result, EnsembleRetriever)


class TestGetDynamicWeights:
    async def test_no_query_returns_defaults(self):
        assert await HybridRetriever.get_dynamic_weights(None) == [0.5, 0.5]
        assert await HybridRetriever.get_dynamic_weights("") == [0.5, 0.5]

    async def test_long_query_biases_vector(self):
        # 长度 > 50 → 基础 [0.7, 0.3]；无空格时词密度低，保持权重
        query = "很" * 60
        assert await HybridRetriever.get_dynamic_weights(query) == [0.7, 0.3]

    async def test_long_query_with_dense_words_adjusts(self):
        # 长度 > 50 且有大量空格分词 → 词密度 > 0.1 → [0.6, 0.4]
        query = "a " * 30  # 60 字符，30 个词 → 密度 0.5
        assert await HybridRetriever.get_dynamic_weights(query) == [0.6, 0.4]

    async def test_short_query_biases_bm25(self):
        # 长度 < 20 → 基础 [0.3, 0.7]；无空格 → 保持
        assert await HybridRetriever.get_dynamic_weights("短查询") == [0.3, 0.7]

    async def test_short_query_with_dense_words_adjusts(self):
        # 长度 < 20 且有空格 → 密度 > 0.1 → bm25 上限 0.7 → [0.3, 0.7]
        assert await HybridRetriever.get_dynamic_weights("hello world") == [0.3, 0.7]

    async def test_medium_query_with_dense_words_adjusts(self):
        # 长度 20~50 基础 [0.5, 0.5]；含空格分词 → 密度 0.21 > 0.1 → [0.4, 0.6]
        assert await HybridRetriever.get_dynamic_weights("this is a medium length query text") == [0.4, 0.6]