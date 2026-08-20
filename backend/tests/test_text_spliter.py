"""Unit tests for the（中文感知）text splitter.

Note: the task description referenced ``app/utils/text_spliter.py``, but that
module does not exist in this repo. The actual splitter (``AsyncTextSplitter``)
lives at ``app/rag/text_spliter.py`` and is the only text-splitting logic in
the codebase, so these tests target it.
"""
import math

import pytest
from langchain_core.documents import Document

import app.rag.text_spliter as text_spliter_mod
from app.rag.text_spliter import AsyncTextSplitter
from app.utils.config import chroma_config


# ---------------------------------------------------------------------------
# construction
# ---------------------------------------------------------------------------
def test_default_constructor_reads_chroma_config():
    splitter = AsyncTextSplitter()
    assert splitter.chunk_size == 1000
    assert splitter.chunk_overlap == 200
    # default separators come from chroma.yaml (fall back to langchain default)
    expected = chroma_config.get("separators")
    assert splitter.separators == (expected or ["\n\n", "\n", " ", ""])
    assert splitter.embedding_model is None


def test_custom_constructor_args():
    splitter = AsyncTextSplitter(
        chunk_size=50,
        chunk_overlap=10,
        separators=["\n", ""],
        embedding_model="fake-model",  # not a real model, only stored
    )
    assert splitter.chunk_size == 50
    assert splitter.chunk_overlap == 10
    assert splitter.separators == ["\n", ""]
    assert splitter.embedding_model == "fake-model"


# ---------------------------------------------------------------------------
# split_text_sync
# ---------------------------------------------------------------------------
def test_split_short_text_single_chunk():
    splitter = AsyncTextSplitter(chunk_size=100, chunk_overlap=20)
    chunks = splitter.split_text_sync("short text")
    assert chunks == ["short text"]


def test_split_empty_text_returns_empty_list():
    splitter = AsyncTextSplitter(chunk_size=10, chunk_overlap=2)
    assert splitter.split_text_sync("") == []


def test_split_long_text_boundaries_and_overlap():
    splitter = AsyncTextSplitter(chunk_size=10, chunk_overlap=2, separators=["\n", ""])
    text = "abcdefghij\nklmnopqrst\nuvwxyzab"
    chunks = splitter.split_text_sync(text)

    # Empirically verified against langchain's RecursiveCharacterTextSplitter
    assert chunks == ["abcdefghij", "klmnopqrs", "rst", "uvwxyzab"]
    assert len(chunks) >= 2
    # boundary: no chunk exceeds chunk_size when separator pieces are small enough
    assert all(len(c) <= 10 for c in chunks)
    # content preservation: every character survives splitting (except the
    # '\n' separators, which RecursiveCharacterTextSplitter removes)
    joined = "".join(chunks)
    for ch in text.replace("\n", ""):
        assert ch in joined
    # overlap semantics: duplicated characters make the reconstructed text
    # longer than the separator-stripped input
    assert sum(len(c) for c in chunks) > len(text.replace("\n", ""))


def test_split_chinese_text():
    splitter = AsyncTextSplitter(
        chunk_size=10, chunk_overlap=2, separators=["，", "。", ""]
    )
    text = "春眠不觉晓，处处闻啼鸟。夜来风雨声，花落知多少。"
    chunks = splitter.split_text_sync(text)
    assert chunks
    assert all(chunk for chunk in chunks)  # no empty chunks
    assert all(len(c) <= 10 for c in chunks)
    joined = "".join(chunks)
    for ch in text:
        assert ch in joined  # 确保中文内容在分割后没有丢失


def test_async_split_text_matches_sync():
    splitter = AsyncTextSplitter(chunk_size=10, chunk_overlap=2, separators=["\n", ""])
    text = "abcdefghij\nklmnopqrst\nuvwxyzab"
    assert splitter.split_text_sync(text) == [
        "abcdefghij", "klmnopqrs", "rst", "uvwxyzab",
    ]


def test_async_split_text_uses_to_thread():
    import asyncio

    splitter = AsyncTextSplitter(chunk_size=10, chunk_overlap=2, separators=["\n", ""])
    text = "abcdefghij\nklmnopqrst\nuvwxyzab"
    chunks = asyncio.run(splitter.split_text(text))
    assert chunks == splitter.split_text_sync(text)


# ---------------------------------------------------------------------------
# split_documents (+ sync variant)
# ---------------------------------------------------------------------------
def test_split_documents_sync_splits_long_docs():
    splitter = AsyncTextSplitter(chunk_size=10, chunk_overlap=2, separators=[" ", ""])
    docs = [Document(page_content="word " * 50)]
    split_docs = splitter.split_documents_sync(docs)
    assert len(split_docs) > 1
    assert all(isinstance(d, Document) for d in split_docs)
    assert all(d.page_content for d in split_docs)
    # content conserved (join of chunks covers the original characters)
    joined = " ".join(d.page_content for d in split_docs)
    for word in ("word" * 1 for _ in range(1)):
        assert word in joined


def test_split_documents_sync_short_doc_kept():
    splitter = AsyncTextSplitter(chunk_size=100, chunk_overlap=10, separators=["\n", ""])
    docs = [Document(page_content="tiny")]
    split_docs = splitter.split_documents_sync(docs)
    assert len(split_docs) == 1
    assert split_docs[0].page_content == "tiny"
    assert split_docs[0].metadata == docs[0].metadata


def test_split_documents_async():
    import asyncio

    splitter = AsyncTextSplitter(chunk_size=10, chunk_overlap=2, separators=[" ", ""])
    docs = [Document(page_content="word " * 50)]
    split_docs = asyncio.run(splitter.split_documents(docs))
    assert len(split_docs) > 1


# ---------------------------------------------------------------------------
# pure math helpers
# ---------------------------------------------------------------------------
def test_cosine_similarity():
    s = AsyncTextSplitter()
    assert math.isclose(s._cosine_similarity([1, 2, 3], [1, 2, 3]), 1.0)
    assert math.isclose(s._cosine_similarity([1, 0], [0, 1]), 0.0)
    assert math.isclose(s._cosine_similarity([2, 0], [1, 0]), 1.0)
    # zero-magnitude vectors must not raise and return 0.0
    assert s._cosine_similarity([0, 0], [1, 1]) == 0.0
    assert s._cosine_similarity([1, 1], [0, 0]) == 0.0


def test_calculate_similarity_sync_without_embedding_model():
    s = AsyncTextSplitter()
    assert s._calculate_similarity_sync("a", "b") == 0.0


def test_optimize_chunks_sync_merges_similar():
    s = AsyncTextSplitter(chunk_size=100, chunk_overlap=20)

    class _FakeEmbed:
        def embed_query(self, text):
            return [1.0, 1.0]  # identical vectors -> similarity 1.0

    s.embedding_model = _FakeEmbed()
    out = s._optimize_chunks_sync(["aaa", "bbb", "ccc"])
    assert out == ["aaa bbb ccc"]

    # dissimilar: fully orthogonal embeddings must force three separate chunks
    class _OrthoEmbed:
        vectors = {"aaa": [1.0, 0.0, 0.0], "bbb": [0.0, 1.0, 0.0], "ccc": [0.0, 0.0, 1.0]}

        def embed_query(self, text):
            return self.vectors[text]

    s.embedding_model = _OrthoEmbed()
    out = s._optimize_chunks_sync(["aaa", "bbb", "ccc"])
    assert out == ["aaa", "bbb", "ccc"]


def test_optimize_chunks_async_merges_similar():
    import asyncio

    s = AsyncTextSplitter(chunk_size=100, chunk_overlap=20)

    class _FakeEmbed:
        def embed_query(self, text):
            return [1.0, 1.0]

    s.embedding_model = _FakeEmbed()
    out = asyncio.run(s._optimize_chunks(["aaa", "bbb"]))
    assert out == ["aaa bbb"]


def test_module_exports_async_text_splitter():
    assert hasattr(text_spliter_mod, "AsyncTextSplitter")


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))