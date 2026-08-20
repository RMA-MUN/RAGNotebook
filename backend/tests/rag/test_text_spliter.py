"""text_spliter.py — AsyncTextSplitter 分割行为测试（含中文文本与嵌入优化）。"""
import pytest
from langchain_core.documents import Document

from app.rag.text_spliter import AsyncTextSplitter
from app.utils.config import chroma_config


class FakeEmbedder:
    """可配置向量的假嵌入模型（duck-typed Embeddings）。"""

    def __init__(self, vectors=None):
        self.vectors = vectors or {}

    def embed_query(self, text):
        return self.vectors.get(text, [1.0, 0.0])

    def embed_documents(self, texts):
        return [self.embed_query(t) for t in texts]


class TestConstructor:
    def test_defaults_from_chroma_config(self):
        splitter = AsyncTextSplitter()
        assert splitter.chunk_size == 1000  # 构造函数签名默认值，同时与 chroma_config 一致
        assert splitter.chunk_overlap == 200  # 构造函数签名默认值（chroma.yaml 中为 20，仅作配置）
        assert splitter.separators == chroma_config["separators"]
        assert splitter.embedding_model is None

    def test_custom_params(self):
        splitter = AsyncTextSplitter(chunk_size=50, chunk_overlap=10, separators=["\n", " "])
        assert splitter.chunk_size == 50
        assert splitter.chunk_overlap == 10
        assert splitter.separators == ["\n", " "]
        # 传递给底层 RecursiveCharacterTextSplitter（v1.x 使用私有属性）
        assert splitter.splitter._chunk_size == 50
        assert splitter.splitter._chunk_overlap == 10


class TestSplitText:
    def test_split_text_sync_respects_chunk_size(self):
        splitter = AsyncTextSplitter(chunk_size=100, chunk_overlap=20)
        chunks = splitter.split_text_sync("word " * 60)  # 240 字符
        assert len(chunks) > 1
        assert all(len(c) <= 100 for c in chunks)

    async def test_split_text_async(self):
        splitter = AsyncTextSplitter(chunk_size=100, chunk_overlap=20)
        chunks = await splitter.split_text("word " * 60)
        assert len(chunks) > 1
        assert all(len(c) <= 100 for c in chunks)

    def test_split_text_chinese_preserves_structure(self):
        # 中文分隔符（。！？）用于保留句子结构，切分后可无损拼接回原文
        splitter = AsyncTextSplitter(chunk_size=6, chunk_overlap=1)
        text = "第一句话。第二句话！第三句话？第四句话。"
        chunks = splitter.split_text_sync(text)
        assert len(chunks) > 1
        assert "".join(chunks) == text
        assert all(len(c) <= 6 for c in chunks)
        # 除首个 chunk 外，其余 chunk 都以中文标点开头（分隔符保留在下一段）
        for c in chunks[1:]:
            assert c[0] in "。！？"


class TestSplitDocuments:
    def test_split_documents_sync_keeps_metadata(self):
        splitter = AsyncTextSplitter(chunk_size=100, chunk_overlap=20)
        doc = Document(page_content="paragraph " * 30, metadata={"source": "a.txt", "md5": "m1"})
        result = splitter.split_documents_sync([doc])
        assert len(result) > 1
        for chunk in result:
            assert len(chunk.page_content) <= 100
            assert chunk.metadata["source"] == "a.txt"
            assert chunk.metadata["md5"] == "m1"

    async def test_split_documents_async(self):
        splitter = AsyncTextSplitter(chunk_size=100, chunk_overlap=20)
        doc = Document(page_content="paragraph " * 30, metadata={"source": "b.md"})
        result = await splitter.split_documents([doc])
        assert len(result) > 1
        assert all(d.metadata["source"] == "b.md" for d in result)


class TestSimilarityAndOptimization:
    def test_cosine_similarity(self):
        splitter = AsyncTextSplitter()
        assert splitter._cosine_similarity([1, 0, 0], [1, 0, 0]) == pytest.approx(1.0)
        assert splitter._cosine_similarity([1, 0], [0, 1]) == pytest.approx(0.0)
        # 零向量 → 0.0（避免除零）
        assert splitter._cosine_similarity([0, 0], [1, 1]) == pytest.approx(0.0)
        assert splitter._cosine_similarity([2, 2], [1, 1]) == pytest.approx(1.0)

    def test_calculate_similarity_sync_without_model_returns_zero(self):
        splitter = AsyncTextSplitter()
        assert splitter._calculate_similarity_sync("x", "y") == 0.0

    def test_calculate_similarity_sync_with_model(self):
        emb = FakeEmbedder({"x": [1, 0], "y": [0.5, 0.5]})
        splitter = AsyncTextSplitter(embedding_model=emb)
        assert splitter._calculate_similarity_sync("x", "y") == pytest.approx(0.7071, abs=1e-3)

    def test_optimize_chunks_sync_merges_similar_ones(self):
        # 默认向量完全相同 → 相似度 1.0 > 0.7 → 全部合并
        splitter = AsyncTextSplitter(embedding_model=FakeEmbedder())
        assert splitter._optimize_chunks_sync(["a", "b", "c"]) == ["a b c"]

    def test_optimize_chunks_sync_keeps_dissimilar_ones(self):
        emb = FakeEmbedder({"a": [1, 0], "b": [0, 1], "c": [1, 0]})
        splitter = AsyncTextSplitter(embedding_model=emb)
        assert splitter._optimize_chunks_sync(["a", "b", "c"]) == ["a", "b", "c"]

    async def test_optimize_chunks_async(self):
        splitter = AsyncTextSplitter(embedding_model=FakeEmbedder())
        assert await splitter._optimize_chunks(["a", "b"]) == ["a b"]

    async def test_split_text_with_embedding_optimizes(self):
        # 所有片段默认向量相同 → 相似度 1.0 → 优化为单个 chunk
        splitter = AsyncTextSplitter(
            chunk_size=100, chunk_overlap=20, embedding_model=FakeEmbedder()
        )
        chunks = await splitter.split_text("word " * 60)
        assert len(chunks) == 1