"""知识库 API 集成测试（fake KnowledgeService + 真实图片服务端点）。"""
import base64

from fastapi import HTTPException
from langchain_core.documents import Document

from app.rag.sse_models import SSEEvent
from app.router.knowledge_service import KnowledgeService
from tests.conftest import install_fake_vector_store
from tests.fakes import TEST_USER_ID


class FakeKnowledgeService(KnowledgeService):
    """大部分处理逻辑打桩，handle_get_batch_images 走真实实现（读取磁盘目录）。"""

    def __init__(self):
        super().__init__()
        self.calls = []

    async def handle_add_vector_single(self, file, user_id):
        self.calls.append(("single", file.filename))
        return file.filename

    async def handle_add_vector_multiple(self, files, user_id):
        self.calls.append(("multiple", [f.filename for f in files]))
        return [f.filename for f in files]

    async def handle_add_vector_multiple_stream(self, files, user_id):
        yield SSEEvent(event_type="start", total_files=len(files), message="开始处理", progress=0).to_sse()
        yield SSEEvent(event_type="finish", total_files=len(files), success_count=1,
                       failed_count=0, message="处理完成", progress=100).to_sse()

    async def clean_user_upload(self, user_id):
        self.calls.append(("clean", user_id))

    async def handle_clear_user_md5(self, user_id, delete_documents=True):
        self.calls.append(("clear_md5", user_id, delete_documents))

    async def handle_delete_single_md5(self, user_id, md5_value, delete_documents=True):
        self.calls.append(("del_md5", md5_value, delete_documents))
        return md5_value == "exists"

    async def handle_delete_by_filename(self, user_id, filename, delete_documents=True):
        self.calls.append(("del_filename", filename, delete_documents))
        return filename == "exists.txt"

    async def handle_get_md5_info(self, user_id, md5_value):
        if md5_value == "exists":
            return {"md5": md5_value, "filename": "a.txt", "original_filename": "a.txt", "upload_time": "2026-01-01"}
        return None

    async def handle_get_all_md5_records(self, user_id):
        return [{"md5": "m1", "filename": "a.txt", "original_filename": "a.txt", "upload_time": None}]

    async def handle_get_user_knowledge(self, user_id):
        return [{"id": "doc1", "filename": "a.txt", "original_filename": "a.txt",
                 "user_id": user_id, "chunk_count": 2, "preview": "预览", "created_at": None}]

    async def handle_get_document_detail(self, user_id, filename):
        if filename == "missing.txt":
            raise HTTPException(status_code=404, detail=f"文档 {filename} 不存在")
        return {"id": "doc1", "filename": filename, "user_id": user_id, "chunk_count": 1,
                "content": "内容", "chunks": [], "images": [], "created_at": None}

    async def handle_get_document_chunks(self, user_id, filename):
        if filename == "missing.txt":
            raise HTTPException(status_code=404, detail=f"文档 {filename} 不存在")
        return {"filename": filename, "total_chunks": 1, "chunks": [{
            "chunk_id": "c1", "index": 0, "content": "切片", "metadata": {}, "images": []}]}


def install_fake_knowledge_service(monkeypatch):
    from main import app

    import app.router.knowledge_router as kr

    service = FakeKnowledgeService()
    app.dependency_overrides[kr.get_knowledge_service] = lambda: service
    return service


# ---------------------------------------------------------------------------
# 上传
# ---------------------------------------------------------------------------
async def test_add_single(client, monkeypatch):
    service = install_fake_knowledge_service(monkeypatch)
    resp = await client.post(
        "/knowledge/add/single",
        files={"file": ("a.txt", b"hello world", "text/plain")},
        headers={"Authorization": "Bearer x"},
    )
    assert resp.status_code == 200
    assert "a.txt 已成功上传" in resp.json()["message"]
    assert service.calls == [("single", "a.txt")]


async def test_add_multiple(client, monkeypatch):
    service = install_fake_knowledge_service(monkeypatch)
    resp = await client.post(
        "/knowledge/add/multiple",
        files=[("files", ("a.txt", b"aaa", "text/plain")), ("files", ("b.md", b"bbb", "text/markdown"))],
        headers={"Authorization": "Bearer x"},
    )
    assert resp.status_code == 200
    assert "['a.txt', 'b.md']" in resp.json()["message"]


async def test_add_multiple_stream(client, monkeypatch):
    install_fake_knowledge_service(monkeypatch)
    async with client.stream(
        "POST", "/knowledge/add/multiple/stream",
        files=[("files", ("a.txt", b"aaa", "text/plain"))],
        headers={"Authorization": "Bearer x"},
    ) as resp:
        assert resp.status_code == 200
        lines = [l async for l in resp.aiter_lines()]
    frames = [l for l in lines if l.startswith("event: ") or l.startswith("data: ")]
    assert frames


# ---------------------------------------------------------------------------
# 删除
# ---------------------------------------------------------------------------
async def test_clean_vectors(client, monkeypatch):
    service = install_fake_knowledge_service(monkeypatch)
    resp = await client.delete("/knowledge/clean", headers={"Authorization": "Bearer x"})
    assert resp.status_code == 200
    assert "已成功删除用户上传的所有向量" in resp.json()["message"]
    assert service.calls == [("clean", TEST_USER_ID)]


async def test_clear_md5(client, monkeypatch):
    service = install_fake_knowledge_service(monkeypatch)
    resp = await client.delete("/knowledge/md5/clear?delete_documents=true", headers={"Authorization": "Bearer x"})
    assert "已成功清空用户的MD5记录和知识库文档" in resp.json()["message"]
    assert service.calls == [("clear_md5", TEST_USER_ID, True)]


async def test_delete_single_md5(client, monkeypatch):
    service = install_fake_knowledge_service(monkeypatch)
    resp = await client.delete("/knowledge/md5/delete/exists", headers={"Authorization": "Bearer x"})
    assert resp.status_code == 200
    assert "已成功删除MD5记录 exists" in resp.json()["message"]

    resp = await client.delete("/knowledge/md5/delete/not-exists", headers={"Authorization": "Bearer x"})
    assert resp.status_code == 404
    assert "不存在" in resp.json()["message"]


async def test_delete_by_filename(client, monkeypatch):
    service = install_fake_knowledge_service(monkeypatch)
    resp = await client.delete("/knowledge/delete/filename?filename=exists.txt", headers={"Authorization": "Bearer x"})
    assert resp.status_code == 200

    resp = await client.delete("/knowledge/delete/filename?filename=no.txt", headers={"Authorization": "Bearer x"})
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# 查询
# ---------------------------------------------------------------------------
async def test_md5_list(client, monkeypatch):
    install_fake_knowledge_service(monkeypatch)
    resp = await client.get("/knowledge/md5/list", headers={"Authorization": "Bearer x"})
    body = resp.json()["data"]
    assert body["total_count"] == 1
    assert body["records"][0]["md5"] == "m1"


async def test_md5_info(client, monkeypatch):
    install_fake_knowledge_service(monkeypatch)
    resp = await client.get("/knowledge/md5/exists", headers={"Authorization": "Bearer x"})
    assert resp.status_code == 200
    assert resp.json()["data"]["filename"] == "a.txt"

    resp = await client.get("/knowledge/md5/nope", headers={"Authorization": "Bearer x"})
    assert resp.status_code == 404


async def test_knowledge_list(client, monkeypatch):
    install_fake_knowledge_service(monkeypatch)
    resp = await client.get("/knowledge/list", headers={"Authorization": "Bearer x"})
    body = resp.json()["data"]
    assert body["total_count"] == 1
    assert body["documents"][0]["filename"] == "a.txt"


async def test_document_detail(client, monkeypatch):
    install_fake_knowledge_service(monkeypatch)
    resp = await client.get("/knowledge/detail?filename=a.txt", headers={"Authorization": "Bearer x"})
    assert resp.status_code == 200
    assert resp.json()["data"]["content"] == "内容"

    resp = await client.get("/knowledge/detail?filename=missing.txt", headers={"Authorization": "Bearer x"})
    assert resp.status_code == 404


async def test_document_chunks(client, monkeypatch):
    install_fake_knowledge_service(monkeypatch)
    resp = await client.get("/knowledge/chunks?filename=a.txt", headers={"Authorization": "Bearer x"})
    assert resp.status_code == 200
    assert resp.json()["data"]["total_chunks"] == 1

    resp = await client.get("/knowledge/chunks?filename=missing.txt", headers={"Authorization": "Bearer x"})
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# 图片
# ---------------------------------------------------------------------------
async def test_serve_image(client, monkeypatch, tmp_path, session_factory):
    import app.router.knowledge_router as kr

    image_dir = tmp_path / "img"
    image_dir.mkdir(parents=True)
    (image_dir / "p0_i0.png").write_bytes(b"\x89PNG fake")

    monkeypatch.setattr(kr, "get_image_storage_dir", lambda u, m: str(image_dir))

    resp = await client.get("/knowledge/image/md5abc/p0_i0.png", headers={"Authorization": "Bearer x"})
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/png"
    assert resp.content == b"\x89PNG fake"


async def test_serve_image_missing(client, monkeypatch, tmp_path):
    import app.router.knowledge_router as kr

    image_dir = tmp_path / "img"
    image_dir.mkdir(parents=True)
    monkeypatch.setattr(kr, "get_image_storage_dir", lambda u, m: str(image_dir))

    resp = await client.get("/knowledge/image/md5abc/nope.png", headers={"Authorization": "Bearer x"})
    assert resp.status_code == 404


async def test_batch_images(client, monkeypatch, tmp_path):
    install_fake_knowledge_service(monkeypatch)

    # 真实 handle_get_batch_images 读取磁盘目录 → 重定向 data 路径
    import app.utils.path_tool as pt
    monkeypatch.setattr(pt, "get_data_path", lambda: str(tmp_path))

    img_dir = tmp_path / "extracted_images" / TEST_USER_ID / "md5abc"
    img_dir.mkdir(parents=True)
    (img_dir / "p0_i0.png").write_bytes(b"\x89PNG fake")

    resp = await client.get("/knowledge/images/all/md5abc", headers={"Authorization": "Bearer x"})
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["md5"] == "md5abc"
    assert "p0_i0.png" in data["images"]
    assert data["images"]["p0_i0.png"].startswith("data:image/png;base64,")
    payload = base64.b64decode(data["images"]["p0_i0.png"].split(",", 1)[1])
    assert payload == b"\x89PNG fake"