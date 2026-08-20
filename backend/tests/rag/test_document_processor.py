"""document_handler/processor.py — DocumentProcessor 测试（loader 全部打桩）。"""
import pytest
from langchain_core.documents import Document

import app.rag.document_handler.processor as proc_module
from app.rag.document_handler.processor import DocumentProcessor
from tests.fakes import FakeChromaStore


def _processor():
    return DocumentProcessor(FakeChromaStore(), md5_store=object())


async def _async_loader(path, *args, **kwargs):
    return [Document(page_content=f"loaded:{path}")]


def _sync_loader(path, *args, **kwargs):
    return [Document(page_content=f"loaded-sync:{path}")]


@pytest.fixture
def stub_loaders(monkeypatch):
    """把模块内引用的所有 loader 替换为打桩函数。"""
    for name in [
        "txt_loader", "pdf_loader", "markdown_loader", "ppt_loader", "word_loader",
        "pdf_multimodal_loader",
    ]:
        monkeypatch.setattr(proc_module, name, _async_loader)
    for name in [
        "txt_loader_sync", "pdf_loader_sync", "markdown_loader_sync",
        "ppt_loader_sync", "word_loader_sync", "pdf_multimodal_loader_sync",
    ]:
        monkeypatch.setattr(proc_module, name, _sync_loader)


class TestGetFileDocument:
    async def test_txt_dispatch(self, stub_loaders, monkeypatch):
        calls = []
        monkeypatch.setattr(proc_module, "txt_loader", lambda p: (calls.append(p) or _async_loader(p)))
        docs = await _processor().get_file_document("note.txt")
        assert calls == ["note.txt"]
        assert docs[0].page_content == "loaded:note.txt"

    async def test_pdf_without_md5_uses_pdf_loader(self, stub_loaders, monkeypatch):
        calls = []
        monkeypatch.setattr(proc_module, "pdf_loader", lambda p: (calls.append(p) or _async_loader(p)))
        docs = await _processor().get_file_document("doc.pdf")
        assert calls == ["doc.pdf"]
        assert isinstance(docs, list)

    async def test_pdf_with_md5_and_user_uses_multimodal_loader(self, stub_loaders, monkeypatch):
        calls = {}
        async def _mm(path, md5, user):
            calls["path"], calls["md5"], calls["user"] = path, md5, user
            return [Document(page_content="mm-doc")]
        monkeypatch.setattr(proc_module, "pdf_multimodal_loader", _mm)
        docs = await _processor().get_file_document("doc.pdf", "md5-1", "user-1")
        assert calls == {"path": "doc.pdf", "md5": "md5-1", "user": "user-1"}
        assert docs[0].page_content == "mm-doc"

    async def test_markdown_dispatch(self, stub_loaders, monkeypatch):
        calls = []
        monkeypatch.setattr(proc_module, "markdown_loader", lambda p: (calls.append(p) or _async_loader(p)))
        await _processor().get_file_document("readme.md")
        assert calls == ["readme.md"]

    async def test_pptx_dispatch(self, stub_loaders, monkeypatch):
        calls = []
        monkeypatch.setattr(proc_module, "ppt_loader", lambda p: (calls.append(p) or _async_loader(p)))
        await _processor().get_file_document("slides.pptx")
        assert calls == ["slides.pptx"]

    async def test_docx_dispatch(self, stub_loaders, monkeypatch):
        calls = []
        monkeypatch.setattr(proc_module, "word_loader", lambda p: (calls.append(p) or _async_loader(p)))
        await _processor().get_file_document("doc.docx")
        assert calls == ["doc.docx"]

    async def test_unknown_extension_returns_empty(self, stub_loaders):
        assert await _processor().get_file_document("unknown.xyz") == []


class TestGetFileDocumentSync:
    def test_txt_dispatch_sync(self, stub_loaders, monkeypatch):
        calls = []
        monkeypatch.setattr(proc_module, "txt_loader_sync", lambda p: (calls.append(p) or _sync_loader(p)))
        docs = _processor().get_file_document_sync("note.txt")
        assert calls == ["note.txt"]
        assert docs[0].page_content == "loaded-sync:note.txt"

    def test_pdf_sync_uses_multimodal_when_md5_and_user(self, stub_loaders, monkeypatch):
        calls = {}
        def _mm(path, md5, user):
            calls.update(path=path, md5=md5, user=user)
            return [Document(page_content="mm")]
        monkeypatch.setattr(proc_module, "pdf_multimodal_loader_sync", _mm)
        docs = _processor().get_file_document_sync("doc.pdf", "md5-1", "user-1")
        assert calls == {"path": "doc.pdf", "md5": "md5-1", "user": "user-1"}
        assert docs[0].page_content == "mm"

    def test_unknown_extension_returns_empty_sync(self, stub_loaders):
        assert _processor().get_file_document_sync("unknown.xyz") == []


class TestSplitDocumentsSync:
    def test_long_document_splits_into_chunks(self):
        processor = _processor()
        doc = Document(page_content="段落内容。" * 500, metadata={"source": "a.txt"})
        chunks = processor.split_documents_sync([doc])
        assert len(chunks) > 1
        # chunk_size 来自 chroma_config（1000）
        assert all(len(c.page_content) <= 1000 for c in chunks)
        # 元数据被保留到每个 chunk
        assert all(c.metadata["source"] == "a.txt" for c in chunks)

    def test_short_document_stays_single_chunk(self):
        processor = _processor()
        doc = Document(page_content="短内容")
        chunks = processor.split_documents_sync([doc])
        assert len(chunks) == 1
        assert chunks[0].page_content == "短内容"

    def test_empty_documents(self):
        processor = _processor()
        assert processor.split_documents_sync([]) == []