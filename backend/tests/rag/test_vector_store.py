"""vector_store.py — 知识库文档服务测试（Neo4j 查询/删除方法）。

不实例化真实服务（禁止触碰真实 Neo4j/MySQL）：
- Neo4j 查询用 FakeDriver 返回预置 records；
- 通过 object.__new__ 构造服务并注入重定向的 MD5Store。
"""
from types import SimpleNamespace

import pytest

import app.rag.vector_store as vs_module
from app.rag.md5_manager.md5_store import MD5Store
from app.rag.vector_store import VectorStoreService


def _rec(**kwargs):
    """模拟 neo4j Record（按 key 取值）。"""
    return kwargs


class FakeDriver:
    """execute_query 返回预置 records。"""

    def __init__(self, records):
        self.records = records
        self.queries = []

    async def execute_query(self, query, params=None):
        self.queries.append({"query": query, "params": params or {}})
        return SimpleNamespace(records=list(self.records))


@pytest.fixture
def service_factory(tmp_path, monkeypatch):
    """构造"手搓"的 VectorStoreService（绕过单例初始化）。"""
    monkeypatch.setattr(vs_module, "delete_image_directory", lambda u, m: True)
    monkeypatch.setattr(vs_module, "delete_user_all_images", lambda u: True)

    class EmptySession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            pass

        async def execute(self, _stmt):
            return SimpleNamespace(all=lambda: [])

    # get_user_documents lazily imports this factory; keep all tests off real MySQL.
    monkeypatch.setattr("app.db.db_config.AsyncSessionLocal", EmptySession)

    def _make(driver_records=None):
        service = object.__new__(VectorStoreService)
        service.md5_store = MD5Store()
        service.md5_store.base_dir = str(tmp_path / "md5store")
        driver = FakeDriver(driver_records or [])
        monkeypatch.setattr(vs_module.VectorStoreService, "_neo4j_driver", staticmethod(lambda: driver))
        return service, driver, service.md5_store

    return _make


class TestGetUserDocuments:
    async def test_aggregates_chunks_by_doc(self, service_factory, monkeypatch):
        service, _, _ = service_factory([
            _rec(id="m1", filename="a.pdf", user_id="u1", chunk_count=2,
                 first_text="A" * 150),
            _rec(id="m2", filename="b.txt", user_id="u1", chunk_count=1,
                 first_text="preview me"),
        ])
        result = await service.get_user_documents("u1")
        assert len(result) == 2
        by_name = {r["filename"]: r for r in result}

        a = by_name["a.pdf"]
        assert a["id"] == "m1"
        assert a["chunk_count"] == 2
        assert a["user_id"] == "u1"
        assert a["original_filename"] == "a.pdf"
        # 长内容预览截断为 100 字符 + "..."
        assert a["preview"] == "A" * 100 + "..."

        b = by_name["b.txt"]
        assert b["chunk_count"] == 1
        assert b["preview"] == "preview me"  # 短内容不截断

    async def test_all_docs_when_user_id_none(self, service_factory, monkeypatch):
        service, driver, _ = service_factory([
            _rec(id="m1", filename="a.pdf", user_id="u1", chunk_count=1, first_text="x"),
        ])
        result = await service.get_user_documents(None)
        assert len(result) == 1
        assert driver.queries[0]["params"]["uid"] is None

    async def test_neo4j_down_returns_empty(self, service_factory, monkeypatch):
        service, _, _ = service_factory()

        def _boom():
            raise RuntimeError("neo4j down")

        monkeypatch.setattr(vs_module.VectorStoreService, "_neo4j_driver", staticmethod(_boom))
        assert await service.get_user_documents("u1") == []


class TestGetDocumentDetail:
    def _records(self):
        doc = {"id": "m1", "user_id": "u1", "filename": "a.pdf", "created_at": 1}
        return [
            _rec(d=doc, c={"id": "doc:m1:0", "text": "chunk one content", "page": 1,
                           "image_paths": ["p1.png", "p0.png"]}),
            _rec(d=doc, c={"id": "doc:m1:1", "text": "chunk two content", "page": 2,
                           "image_paths": ["p1.png"]}),
        ]

    async def test_detail_structure(self, service_factory):
        service, _, _ = service_factory(self._records())

        detail = await service.get_document_detail("u1", "a.pdf")
        assert detail is not None
        assert detail["id"] == "m1"
        assert detail["filename"] == "a.pdf"
        assert detail["user_id"] == "u1"
        assert detail["chunk_count"] == 2
        assert detail["md5"] == "m1"
        assert detail["content"] == "chunk one content\nchunk two content"
        # 图片 URL 去重 + 排序
        assert detail["images"] == [
            "/knowledge/image/m1/p0.png",
            "/knowledge/image/m1/p1.png",
        ]
        # chunk 明细
        assert [c["index"] for c in detail["chunks"]] == [0, 1]
        assert detail["chunks"][0]["chunk_id"] == "doc:m1:0"
        assert detail["chunks"][0]["content"] == "chunk one content"
        assert detail["chunks"][0]["page"] == 1
        # chunk 内图片顺序保持节点属性中的原始顺序（去重/排序只作用于 doc_info["images"]）
        assert detail["chunks"][0]["images"] == [
            "/knowledge/image/m1/p1.png",
            "/knowledge/image/m1/p0.png",
        ]

    async def test_no_chunks_still_returns_doc(self, service_factory):
        doc = {"id": "m1", "user_id": "u1", "filename": "a.pdf"}
        service, _, _ = service_factory([_rec(d=doc, c=None)])
        detail = await service.get_document_detail("u1", "a.pdf")
        assert detail is not None
        assert detail["chunk_count"] == 0
        assert detail["content"] == ""

    async def test_not_found_returns_none(self, service_factory):
        service, _, _ = service_factory([])
        assert await service.get_document_detail("u1", "zzz.pdf") is None


class TestGetDocumentChunks:
    def _records(self):
        doc = {"id": "m1", "user_id": "u1", "filename": "a.pdf"}
        return [
            _rec(d=doc, c={"id": "doc:m1:0", "text": "c1", "page": 1, "image_paths": ["x.png"]}),
            _rec(d=doc, c={"id": "doc:m1:1", "text": "c2", "page": 2, "image_paths": None}),
        ]

    async def test_chunks_structure(self, service_factory):
        service, _, _ = service_factory(self._records())
        result = await service.get_document_chunks("u1", "a.pdf")
        assert result["filename"] == "a.pdf"
        assert result["total_chunks"] == 2
        assert [c["index"] for c in result["chunks"]] == [0, 1]
        assert result["chunks"][0]["chunk_id"] == "doc:m1:0"
        assert result["chunks"][0]["content"] == "c1"
        assert result["chunks"][0]["metadata"]["md5"] == "m1"
        assert result["chunks"][0]["images"] == ["/knowledge/image/m1/x.png"]
        # 没有 image_paths 的 chunk → 空图片列表
        assert result["chunks"][1]["images"] == []

    async def test_missing_filename(self, service_factory):
        service, _, _ = service_factory([])
        result = await service.get_document_chunks("u1", "missing.pdf")
        assert result["filename"] == "missing.pdf"
        assert result["total_chunks"] == 0
        assert result["chunks"] == []


class TestDeletions:
    async def test_delete_user_md5_removes_records_and_graph(self, service_factory, monkeypatch):
        service, _, md5_store = service_factory()
        await md5_store.save_md5_hex("m1", "a.txt", "a.txt", "u1")

        calls = []

        async def _fake_cleanup_all(user_id):
            calls.append(user_id)

        monkeypatch.setattr("app.graph.services.graph_service.cleanup_all_docs_graph",
                            _fake_cleanup_all)
        await service.delete_user_md5("u1")
        # MD5 记录文件被删除 + 图谱清空被调用
        assert await md5_store.get_all_md5_records("u1") == []
        assert calls == ["u1"]

    async def test_delete_by_filename(self, service_factory, monkeypatch):
        service, _, md5_store = service_factory()
        await md5_store.save_md5_hex("m1", "a.txt", "a.txt", "u1")
        await md5_store.save_md5_hex("m2", "b.txt", "b.txt", "u1")

        calls = []

        async def _fake_cleanup(user_id, doc_id):
            calls.append((user_id, doc_id))

        monkeypatch.setattr("app.graph.services.graph_service.cleanup_doc_graph", _fake_cleanup)
        ok = await service.delete_by_filename("u1", "a.txt")
        assert ok is True
        assert calls == [("u1", "m1")]
        # MD5 记录也同步删除
        records = await md5_store.get_all_md5_records("u1")
        assert [r["md5"] for r in records] == ["m2"]

    async def test_delete_by_filename_not_found(self, service_factory, monkeypatch):
        """md5 记录缺失 → 返回 False，仍按文件名兜底清理图谱（防残留）。"""
        service, _, md5_store = service_factory()
        await md5_store.save_md5_hex("m1", "a.txt", "a.txt", "u1")

        fallback_calls = []
        normal_calls = []

        async def _fake_cleanup_by_filename(user_id, filename):
            fallback_calls.append((user_id, filename))

        async def _fake_cleanup(user_id, doc_id):
            normal_calls.append((user_id, doc_id))

        monkeypatch.setattr("app.graph.services.graph_service.cleanup_doc_graph_by_filename",
                            _fake_cleanup_by_filename)
        monkeypatch.setattr("app.graph.services.graph_service.cleanup_doc_graph",
                            _fake_cleanup)

        assert await service.delete_by_filename("u1", "nope.txt") is False
        assert fallback_calls == [("u1", "nope.txt")] and normal_calls == []

        assert await service.delete_by_filename("u1", "a.txt") is True
        assert normal_calls == [("u1", "m1")] and fallback_calls == [("u1", "nope.txt")]

    async def test_delete_single_md5(self, service_factory, monkeypatch):
        service, _, md5_store = service_factory()
        await md5_store.save_md5_hex("m1", "a.txt", "a.txt", "u1")
        await md5_store.save_md5_hex("m2", "b.txt", "b.txt", "u1")

        calls = []

        async def _fake_cleanup(user_id, doc_id):
            calls.append((user_id, doc_id))

        monkeypatch.setattr("app.graph.services.graph_service.cleanup_doc_graph", _fake_cleanup)
        assert await service.delete_single_md5("u1", "m1") is True
        assert calls == [("u1", "m1")]
        # 已删除的 md5 再次删除 → False，但仍触发图谱兜底清理
        assert await service.delete_single_md5("u1", "m1") is False
        assert calls == [("u1", "m1"), ("u1", "m1")]


class TestMd5Passthrough:
    async def test_get_and_list_records(self, service_factory):
        service, _, md5_store = service_factory()
        await md5_store.save_md5_hex("m1", "a.txt", None, "u1")

        info = await service.get_md5_info("u1", "m1")
        assert info["md5"] == "m1"
        assert await service.get_md5_info("u1", "zzz") is None
        assert await service.get_all_md5_records("u1") == [info]

    async def test_check_and_save(self, service_factory):
        service, _, _ = service_factory()
        assert await service.check_md5_hex("m1", "u1") is False
        await service.save_md5_hex("m1", "a.txt", None, "u1")
        assert await service.check_md5_hex("m1", "u1") is True

    def test_save_md5_hex_sync(self, service_factory):
        service, _, _ = service_factory()
        service.save_md5_hex_sync("m2", "b.txt", None, "u1")
        assert service.md5_store.base_dir.endswith("md5store")
