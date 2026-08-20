"""vector_store.py — VectorStoreService 查询/删除方法测试。

不实例化真实 VectorStoreService（禁止真实 Chroma），
通过 object.__new__ 构造并注入 FakeChromaStore / 重定向的 MD5Store。
"""
import pytest
from langchain_core.documents import Document

import app.rag.vector_store as vs_module
from app.rag.md5_manager.md5_store import MD5Store
from app.rag.vector_store import VectorStoreService
from tests.fakes import FakeChromaStore, FakeHybridRetriever


def _doc(content, *, user_id="u1", source, original_filename, md5, page=1, created_at="2024-01-01", images=None):
    meta = {
        "user_id": user_id,
        "source": source,
        "original_filename": original_filename,
        "md5": md5,
        "page": page,
        "created_at": created_at,
    }
    if images is not None:
        meta["image_paths"] = images
    return Document(page_content=content, metadata=meta)


@pytest.fixture
def service_factory(tmp_path, monkeypatch):
    """构造"手搓"的 VectorStoreService（绕过真实 Chroma 初始化）。"""
    # 磁盘图片清理函数打桩，避免触碰真实 data 目录
    monkeypatch.setattr(vs_module, "delete_image_directory", lambda u, m: True)
    monkeypatch.setattr(vs_module, "delete_user_all_images", lambda u: True)

    def _make(documents=None):
        store = FakeChromaStore()
        ids = store.add_documents(documents) if documents else []
        md5_store = MD5Store()
        md5_store.base_dir = str(tmp_path / "md5store")
        service = object.__new__(VectorStoreService)
        service.vectors_store = store
        service.md5_store = md5_store
        service.hybrid_retriever = FakeHybridRetriever(documents=documents or [])
        service.document_processor = None
        return service, store, md5_store, ids

    return _make


class TestComputeRouteScore:
    async def test_returns_score_from_top1_distance(self, service_factory):
        service, store, _, _ = service_factory(
            [_doc("内容A", user_id="u1", source="a.txt", original_filename="a.txt", md5="m1")]
        )
        # FakeChromaStore 固定返回距离 0.5 → 1 / (1 + 0.5)
        score = await service.compute_route_score("查询", "u1")
        assert score == pytest.approx(1 / 1.5)

    async def test_empty_store_returns_zero(self, service_factory):
        service, _, _, _ = service_factory([])
        assert await service.compute_route_score("查询", "u1") == 0.0

    async def test_exception_swallowed_to_zero(self, service_factory):
        service, store, _, _ = service_factory(
            [_doc("A", user_id="u1", source="a.txt", original_filename="a.txt", md5="m1")]
        )
        def _boom(*a, **k):
            raise RuntimeError("chroma 挂了")
        store.similarity_search_with_score = _boom
        assert await service.compute_route_score("查询", "u1") == 0.0


class TestGetUserDocuments:
    def _seed(self, service_factory):
        docs = [
            _doc("A" * 150, user_id="u1", source=r"C:\docs\a.pdf", original_filename="a.pdf", md5="m1", page=1),
            _doc("short", user_id="u1", source=r"C:\docs\a.pdf", original_filename="a.pdf", md5="m1", page=2),
            _doc("preview me", user_id="u1", source="b.txt", original_filename="b.txt", md5="m2", page=1),
        ]
        return service_factory(docs)

    async def test_groups_chunks_by_filename(self, service_factory):
        service, _, _, _ = self._seed(service_factory)
        result = await service.get_user_documents("u1")
        assert len(result) == 2
        by_name = {r["filename"]: r for r in result}

        a = by_name["a.pdf"]
        assert a["chunk_count"] == 2
        assert a["user_id"] == "u1"
        assert a["original_filename"] == "a.pdf"
        assert a["created_at"] == "2024-01-01"
        # 长内容预览截断为 100 字符 + "..."
        assert a["preview"] == "A" * 100 + "..."

        b = by_name["b.txt"]
        assert b["chunk_count"] == 1
        assert b["preview"] == "preview me"  # 短内容不截断

    async def test_all_docs_when_user_id_none(self, service_factory):
        service, _, _, _ = self._seed(service_factory)
        result = await service.get_user_documents(None)
        assert len(result) == 2  # 仍是按文件名分组，两个文件

    async def test_empty_store(self, service_factory):
        service, _, _, _ = service_factory([])
        assert await service.get_user_documents("u1") == []


class TestGetDocumentDetail:
    def _seed(self, service_factory):
        docs = [
            _doc("chunk one content", user_id="u1", source=r"C:\docs\a.pdf",
                 original_filename="a.pdf", md5="m1", page=1, images=["p1.png", "p0.png"]),
            _doc("chunk two content", user_id="u1", source=r"C:\docs\a.pdf",
                 original_filename="a.pdf", md5="m1", page=2, images=["p1.png"]),
            _doc("other content", user_id="u1", source="b.txt", original_filename="b.txt", md5="m2", page=1),
        ]
        return service_factory(docs)

    async def test_detail_structure(self, service_factory):
        service, store, _, ids = self._seed(service_factory)
        a1_id, a2_id = ids[0], ids[1]

        detail = await service.get_document_detail("u1", "a.pdf")
        assert detail is not None
        assert detail["id"] == a1_id
        assert detail["filename"] == "a.pdf"
        assert detail["user_id"] == "u1"
        assert detail["chunk_count"] == 2
        assert detail["md5"] == "m1"
        assert detail["created_at"] == "2024-01-01"
        assert detail["content"] == "chunk one content\nchunk two content"
        # 图片 URL 去重 + 排序
        assert detail["images"] == [
            "/knowledge/image/m1/p0.png",
            "/knowledge/image/m1/p1.png",
        ]
        # chunk 明细
        assert [c["index"] for c in detail["chunks"]] == [0, 1]
        assert detail["chunks"][0]["chunk_id"] == a1_id
        assert detail["chunks"][0]["content"] == "chunk one content"
        assert detail["chunks"][0]["page"] == 1
        # chunk 内图片顺序保持 metadata 中的原始顺序（去重/排序只作用于 doc_info["images"]）
        assert detail["chunks"][0]["images"] == [
            "/knowledge/image/m1/p1.png",
            "/knowledge/image/m1/p0.png",
        ]
        assert detail["chunks"][1]["chunk_id"] == a2_id
        assert detail["chunks"][1]["images"] == ["/knowledge/image/m1/p1.png"]

    async def test_not_found_returns_none(self, service_factory):
        service, _, _, _ = self._seed(service_factory)
        assert await service.get_document_detail("u1", "zzz.pdf") is None


class TestGetDocumentChunks:
    def _seed(self, service_factory):
        docs = [
            _doc("c1", user_id="u1", source="a.pdf", original_filename="a.pdf", md5="m1", page=1, images=["x.png"]),
            _doc("c2", user_id="u1", source="a.pdf", original_filename="a.pdf", md5="m1", page=2),
            _doc("other", user_id="u1", source="b.txt", original_filename="b.txt", md5="m2"),
        ]
        return service_factory(docs)

    async def test_chunks_structure(self, service_factory):
        service, _, _, ids = self._seed(service_factory)
        result = await service.get_document_chunks("u1", "a.pdf")
        assert result["filename"] == "a.pdf"
        assert result["total_chunks"] == 2
        assert [c["index"] for c in result["chunks"]] == [0, 1]
        assert result["chunks"][0]["chunk_id"] == ids[0]
        assert result["chunks"][0]["content"] == "c1"
        assert result["chunks"][0]["metadata"]["md5"] == "m1"
        assert result["chunks"][0]["images"] == ["/knowledge/image/m1/x.png"]
        # 没有 image_paths 的 chunk → 空图片列表
        assert result["chunks"][1]["images"] == []

    async def test_missing_filename(self, service_factory):
        service, _, _, _ = self._seed(service_factory)
        result = await service.get_document_chunks("u1", "missing.pdf")
        assert result["filename"] == "missing.pdf"
        assert result["total_chunks"] == 0
        assert result["chunks"] == []


class TestDeletions:
    async def test_delete_user_md5_removes_docs_and_records(self, service_factory):
        service, store, md5_store, _ = service_factory(
            [_doc("A", user_id="u1", source="a.txt", original_filename="a.txt", md5="m1")]
        )
        await md5_store.save_md5_hex("m1", "a.txt", "a.txt", "u1")

        await service.delete_user_md5("u1")
        # 向量库中该用户的文档被删除
        assert store.get(where={"user_id": "u1"})["ids"] == []
        # MD5 记录文件被删除
        assert await md5_store.get_all_md5_records("u1") == []

    async def test_delete_by_filename(self, service_factory):
        service, store, md5_store, _ = service_factory([
            _doc("A", user_id="u1", source="a.txt", original_filename="a.txt", md5="m1"),
            _doc("B", user_id="u1", source="b.txt", original_filename="b.txt", md5="m2"),
        ])
        await md5_store.save_md5_hex("m1", "a.txt", "a.txt", "u1")
        await md5_store.save_md5_hex("m2", "b.txt", "b.txt", "u1")

        ok = await service.delete_by_filename("u1", "a.txt")
        assert ok is True
        # 只删除该文件的文档（$and: user_id + md5）
        remaining = store.get(where={"user_id": "u1"})
        assert len(remaining["ids"]) == 1
        assert remaining["metadatas"][0]["md5"] == "m2"
        # MD5 记录也同步删除
        records = await md5_store.get_all_md5_records("u1")
        assert [r["md5"] for r in records] == ["m2"]

    async def test_delete_by_filename_not_found(self, service_factory):
        service, store, md5_store, _ = service_factory(
            [_doc("A", user_id="u1", source="a.txt", original_filename="a.txt", md5="m1")]
        )
        await md5_store.save_md5_hex("m1", "a.txt", "a.txt", "u1")
        assert await service.delete_by_filename("u1", "nope.txt") is False
        assert len(store.get(where={"user_id": "u1"})["ids"]) == 1

    async def test_delete_single_md5(self, service_factory):
        service, store, md5_store, _ = service_factory([
            _doc("A", user_id="u1", source="a.txt", original_filename="a.txt", md5="m1"),
            _doc("B", user_id="u1", source="b.txt", original_filename="b.txt", md5="m2"),
        ])
        await md5_store.save_md5_hex("m1", "a.txt", "a.txt", "u1")
        await md5_store.save_md5_hex("m2", "b.txt", "b.txt", "u1")

        assert await service.delete_single_md5("u1", "m1") is True
        remaining = store.get(where={"user_id": "u1"})
        assert [m["md5"] for m in remaining["metadatas"]] == ["m2"]
        # 已删除的 md5 再次删除 → False
        assert await service.delete_single_md5("u1", "m1") is False


class TestMd5Passthrough:
    async def test_get_and_list_records(self, service_factory):
        service, _, md5_store, _ = service_factory([])
        await md5_store.save_md5_hex("m1", "a.txt", None, "u1")

        info = await service.get_md5_info("u1", "m1")
        assert info["md5"] == "m1"
        assert await service.get_md5_info("u1", "zzz") is None
        assert await service.get_all_md5_records("u1") == [info]

    async def test_check_and_save(self, service_factory):
        service, _, _, _ = service_factory([])
        assert await service.check_md5_hex("m1", "u1") is False
        await service.save_md5_hex("m1", "a.txt", None, "u1")
        assert await service.check_md5_hex("m1", "u1") is True

    def test_save_md5_hex_sync(self, service_factory):
        service, _, _, _ = service_factory([])
        service.save_md5_hex_sync("m2", "b.txt", None, "u1")
        assert service.md5_store.base_dir.endswith("md5store")