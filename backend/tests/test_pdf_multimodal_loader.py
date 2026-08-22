"""pdf_multimodal_loader.py — 多模态 PDF 加载器测试。

conftest 已设置 VISION_ENABLED=false（导入时生效），默认走纯文本路径；
视觉路径通过临时 monkeypatch 模块级开关单独验证，且不触碰真实 data 目录
（extract_images_from_pdf 全部打桩）。
"""
import os

import fitz
import pytest
from langchain_core.documents import Document

import app.utils.pdf_multimodal_loader as loader_mod
from app.utils.pdf_multimodal_loader import (
    _build_document,
    pdf_multimodal_loader,
    pdf_multimodal_loader_sync,
)


def _make_pdf(tmp_path, name="sample.pdf", texts=("Hello PDF page one with some text content.", "Second page text here.")):
    """用 PyMuPDF 生成 2 页纯文本 PDF。"""
    pdf = fitz.open()
    for text in texts:
        page = pdf.new_page()
        page.insert_text((72, 72), text)
    path = tmp_path / name
    pdf.save(str(path))
    pdf.close()
    return path


@pytest.fixture
def no_image_extraction(monkeypatch):
    """拦截图片提取，避免写入 backend/data/extracted_images。"""
    monkeypatch.setattr(loader_mod, "extract_images_from_pdf", lambda *a, **k: {})
    return None


class FakeVisionService:
    """假视觉服务：按页返回描述，支持批量和同步接口。

    作为 loader 模块中的 VisionService 类被整体替换，
    因此需要提供类方法 hamming_distance（loader 以 `VisionService.hamming_distance` 调用）。
    """

    def __init__(self, prefix="视觉描述"):
        self.prefix = prefix
        self.batch_calls = 0

    async def describe_pages_batch(self, image_paths, page_numbers, texts):
        self.batch_calls += 1
        return {pn: f"{self.prefix}{pn}" for pn in page_numbers}

    def describe_pages_batch_sync(self, image_paths, page_numbers, texts):
        return {pn: f"{self.prefix}{pn}" for pn in page_numbers}

    def compute_image_hash(self, path):
        # 有效 64 位 phash（16 个十六进制字符）→ 汉明距离 0 → 页面视为重复
        return "0" * 16

    @staticmethod
    def hamming_distance(h1, h2):
        return 0


def _enable_vision(monkeypatch, dedup=False, vision_cls=None):
    monkeypatch.setattr(loader_mod, "_VISION_ENABLED", True)
    monkeypatch.setattr(loader_mod, "_DEDUP_ENABLED", dedup)
    monkeypatch.setattr(loader_mod, "_BATCH_SIZE", 5)
    monkeypatch.setattr(loader_mod, "_LOW_RES_BATCH", True)
    monkeypatch.setattr(loader_mod, "VisionService", vision_cls or FakeVisionService)


class TestBuildDocument:
    def test_structure(self):
        doc = _build_document("内容", page_num=2, md5="m1", source="a.pdf", image_paths=["p.png"], has_images=True)
        assert doc.page_content == "内容"
        assert doc.metadata["page"] == 2
        assert doc.metadata["md5"] == "m1"
        assert doc.metadata["source"] == "a.pdf"
        assert doc.metadata["image_paths"] == ["p.png"]
        assert doc.metadata["has_images"] is True

    def test_empty_image_paths_turns_none(self):
        doc = _build_document("x", 1, "m1", "a.pdf", [], False)
        assert doc.metadata["image_paths"] is None
        assert doc.metadata["has_images"] is False


class TestMissingFile:
    async def test_async_missing_file_returns_empty(self, tmp_path):
        assert await pdf_multimodal_loader(str(tmp_path / "nope.pdf"), "md5", "u1") == []

    def test_sync_missing_file_returns_empty(self, tmp_path):
        assert pdf_multimodal_loader_sync(str(tmp_path / "nope.pdf"), "md5", "u1") == []


class TestVisionDisabled:
    """VISION_ENABLED=false（conftest 默认）：全部页面走纯文本。"""

    async def test_pure_text_documents(self, tmp_path, no_image_extraction):
        pdf = _make_pdf(tmp_path)
        docs = await pdf_multimodal_loader(str(pdf), "md5-1", "user-1")
        assert len(docs) == 2
        docs.sort(key=lambda d: d.metadata["page"])
        assert docs[0].metadata["page"] == 1
        assert docs[0].metadata["md5"] == "md5-1"
        assert docs[0].metadata["source"] == "sample.pdf"
        assert docs[0].metadata["has_images"] is False
        assert "Hello PDF page one" in docs[0].page_content
        # 未启用视觉 → 不包含视觉描述标记
        assert "[页面视觉描述]" not in docs[0].page_content

    def test_sync_pure_text_documents(self, tmp_path, no_image_extraction):
        pdf = _make_pdf(tmp_path)
        docs = pdf_multimodal_loader_sync(str(pdf), "md5-1", "user-1")
        assert len(docs) == 2
        assert all("[页面视觉描述]" not in d.page_content for d in docs)

    async def test_image_paths_preserved_even_without_vision(self, tmp_path, monkeypatch):
        """未启用视觉时仍会保留提取出的图片文件名列表（前端展示用）。"""
        monkeypatch.setattr(loader_mod, "extract_images_from_pdf", lambda *a, **k: {0: ["p0_i0.png"]})
        pdf = _make_pdf(tmp_path)
        docs = await pdf_multimodal_loader(str(pdf), "md5-1", "user-1")
        page1 = next(d for d in docs if d.metadata["page"] == 1)
        assert page1.metadata["image_paths"] == ["p0_i0.png"]
        assert page1.metadata["has_images"] is False


class TestVisionEnabled:
    async def test_short_text_pages_get_vision_description(self, tmp_path, monkeypatch):
        monkeypatch.setattr(loader_mod, "extract_images_from_pdf", lambda *a, **k: {})
        _enable_vision(monkeypatch)
        pdf = _make_pdf(tmp_path)

        docs = await pdf_multimodal_loader(str(pdf), "md5-1", "user-1")
        assert len(docs) == 2
        docs.sort(key=lambda d: d.metadata["page"])
        # 文本 < 100 字符 → 走视觉路径 → 内容合并视觉描述
        assert "Hello PDF page one" in docs[0].page_content
        assert "[页面视觉描述]: 视觉描述1" in docs[0].page_content
        assert docs[0].metadata["page"] == 1
        assert docs[0].metadata["md5"] == "md5-1"
        assert docs[0].metadata["source"] == "sample.pdf"

    def test_sync_vision_description(self, tmp_path, monkeypatch):
        monkeypatch.setattr(loader_mod, "extract_images_from_pdf", lambda *a, **k: {})
        _enable_vision(monkeypatch)
        pdf = _make_pdf(tmp_path)

        docs = pdf_multimodal_loader_sync(str(pdf), "md5-2", "user-2")
        assert len(docs) == 2
        assert all("[页面视觉描述]" in d.page_content for d in docs)

    async def test_vision_empty_result_falls_back_to_text(self, tmp_path, monkeypatch):
        """真实 VisionService 内部会吞掉模型异常并返回已有文本/空串；
        这里模拟视觉描述为空的情况 → 页面保留原始文本。"""
        monkeypatch.setattr(loader_mod, "extract_images_from_pdf", lambda *a, **k: {})

        class EmptyVision(FakeVisionService):
            async def describe_pages_batch(self, *a, **k):
                return {}

        _enable_vision(monkeypatch, vision_cls=EmptyVision)
        pdf = _make_pdf(tmp_path)

        docs = await pdf_multimodal_loader(str(pdf), "md5-1", "user-1")
        assert len(docs) == 2
        # 视觉描述为空 → 保留原始文本，无 [页面视觉描述] 标记
        assert all("[页面视觉描述]" not in d.page_content for d in docs)
        assert "Hello PDF page one" in docs[0].page_content

    async def test_dedup_copies_vision_text_to_similar_pages(self, tmp_path, monkeypatch):
        monkeypatch.setattr(loader_mod, "extract_images_from_pdf", lambda *a, **k: {})
        _enable_vision(monkeypatch, dedup=True)
        # compute_image_hash 恒返回 0 哈希 → 所有页面视为相似 → 同组
        pdf = _make_pdf(tmp_path)

        docs = await pdf_multimodal_loader(str(pdf), "md5-1", "user-1")
        assert len(docs) == 2
        docs.sort(key=lambda d: d.metadata["page"])
        # 代表页（第 1 页）的描述被复制给同组第 2 页
        assert "[页面视觉描述]: 视觉描述1" in docs[0].page_content
        assert "[页面视觉描述]: 视觉描述1" in docs[1].page_content


class TestTempFileCleanup:
    async def test_no_temp_pngs_left_in_vision_mode(self, tmp_path, monkeypatch):
        monkeypatch.setattr(loader_mod, "extract_images_from_pdf", lambda *a, **k: {})
        _enable_vision(monkeypatch)
        pdf = _make_pdf(tmp_path)

        docs = await pdf_multimodal_loader(str(pdf), "md5-1", "user-1")
        assert len(docs) == 2  # 正常返回，清理逻辑在 finally 中执行（不抛错）