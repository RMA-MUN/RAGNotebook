"""笔记 API 集成测试（真实 NoteService + SQLite + FakeChromaStore + 假 LLM）。"""
import asyncio
import json
import zipfile

from tests.fakes import TEST_USER_ID


PASSWORD = None  # unused


def _note_payload(**overrides):
    payload = {"title": "测试笔记", "content": "这是一段测试内容。", "tags": ["测试"], "category": "life"}
    payload.update(overrides)
    return payload


# ---------------------------------------------------------------------------
# 创建
# ---------------------------------------------------------------------------
async def test_create_note(client, real_note_service, session_factory):
    resp = await client.post("/note/create", json=_note_payload(), headers={"Authorization": "Bearer x"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == 200
    assert body["message"] == "笔记创建成功"
    note = body["data"]
    assert note["title"] == "测试笔记"
    assert note["user_id"] == TEST_USER_ID
    assert note["id"]

    # 已入库
    async with session_factory() as s:
        from sqlalchemy import select
        from app.models.note import Note
        result = await s.execute(select(Note).where(Note.id == note["id"]))
        assert result.scalar_one() is not None


async def test_create_note_starts_auto_tag_task(client, real_note_service, session_factory, monkeypatch):
    """未提供 tags/category 时，后台任务自动生成标签并创建回顾记录。"""
    from app.core.background_init import init_manager
    from langchain_core.messages import AIMessage
    from app.models.review_record import ReviewRecord
    from app.models.note import Note
    from sqlalchemy import select

    class CannedChat:
        async def ainvoke(self, messages):
            return AIMessage(content='{"tags": ["ai", "笔记"], "category": "work"}')

    monkeypatch.setattr(init_manager, "chat_model", CannedChat())

    resp = await client.post("/note/create", json=_note_payload(tags=None, category=None),
                             headers={"Authorization": "Bearer x"})
    note_id = resp.json()["data"]["id"]

    # 后台任务的最后一步是创建 ReviewRecord —— 以它出现作为完成信号
    for _ in range(100):
        await asyncio.sleep(0.02)
        async with session_factory() as s:
            r = await s.execute(select(ReviewRecord).where(ReviewRecord.note_id == note_id))
            if r.scalar_one_or_none() is not None:
                break

    async with session_factory() as s:
        result = await s.execute(select(Note).where(Note.id == note_id))
        note = result.scalar_one()
    assert note.tags == ["ai", "笔记"]
    assert note.category == "work"

    async with session_factory() as s:
        result = await s.execute(select(ReviewRecord).where(ReviewRecord.note_id == note_id))
        assert result.scalar_one() is not None


# ---------------------------------------------------------------------------
# 查询
# ---------------------------------------------------------------------------
async def _create_three_notes(client):
    ids = []
    for i in range(3):
        resp = await client.post("/note/create", json=_note_payload(title=f"笔记{i}", tags=None, category=None),
                                 headers={"Authorization": "Bearer x"})
        ids.append(resp.json()["data"]["id"])
    return ids


async def test_list_notes(client, real_note_service):
    await _create_three_notes(client)
    resp = await client.get("/note/list", headers={"Authorization": "Bearer x"})
    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body["total_count"] == 3
    assert len(body["notes"]) == 3


async def test_list_notes_pagination_and_filter(client, real_note_service):
    await _create_three_notes(client)
    resp = await client.get("/note/list?page=1&page_size=2", headers={"Authorization": "Bearer x"})
    body = resp.json()["data"]
    assert body["total_count"] == 3
    assert len(body["notes"]) == 2

    resp = await client.get("/note/list?category=life", headers={"Authorization": "Bearer x"})
    body = resp.json()["data"]
    assert body["total_count"] == 0  # 三个笔记 tags/category 均为空


async def test_list_notes_invalid_sort(client, real_note_service):
    resp = await client.get("/note/list?sort_by=invalid", headers={"Authorization": "Bearer x"})
    assert resp.status_code == 400  # Query pattern 校验失败


async def test_search_notes(client, real_note_service, session_factory, monkeypatch):
    from app.core.background_init import init_manager

    # 预置一条带 metadata 的向量文档
    notes_store = init_manager.note_service.notes_store
    from langchain_core.documents import Document
    notes_store.add_documents(
        [Document(page_content="语义内容", metadata={"user_id": TEST_USER_ID, "note_id": "note-1", "doc_type": "note", "title": "语义笔记"})],
        ids=["note-1"],
    )
    async with session_factory() as s:
        from app.models.note import Note
        s.add(Note(id="note-1", user_id=TEST_USER_ID, title="语义笔记", content="语义内容"))
        await s.commit()

    resp = await client.get("/note/search?q=%E8%AF%AD%E4%B9%89", headers={"Authorization": "Bearer x"})
    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body["total_count"] == 1
    assert body["notes"][0]["id"] == "note-1"


# ---------------------------------------------------------------------------
# 批量操作
# ---------------------------------------------------------------------------
async def test_batch_delete_notes(client, real_note_service):
    ids = await _create_three_notes(client)
    resp = await client.post("/note/batch/delete", json={"ids": ids[:1]}, headers={"Authorization": "Bearer x"})
    assert resp.status_code == 200
    assert "成功删除 1 篇笔记" == resp.json()["message"]


async def test_batch_download_notes_zip(client, real_note_service):
    ids = await _create_three_notes(client)
    resp = await client.post("/note/batch/download", json={"ids": ids}, headers={"Authorization": "Bearer x"})
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/zip"
    assert "attachment" in resp.headers["content-disposition"]
    zf = zipfile.ZipFile(__import__("io").BytesIO(resp.content))
    names = zf.namelist()
    assert len(names) >= 1 and all(n.endswith(".md") for n in names)


async def test_batch_category_and_pin(client, real_note_service):
    ids = await _create_three_notes(client)
    resp = await client.put("/note/batch/category", json={"ids": ids, "category": "study"},
                            headers={"Authorization": "Bearer x"})
    assert "成功更新 3 篇笔记的分类" == resp.json()["message"]

    resp = await client.put("/note/batch/pin", json={"ids": ids, "is_pinned": True},
                            headers={"Authorization": "Bearer x"})
    assert "成功更新 3 篇笔记的置顶状态" == resp.json()["message"]


async def test_stats(client, real_note_service):
    await _create_three_notes(client)
    resp = await client.get("/note/stats", headers={"Authorization": "Bearer x"})
    body = resp.json()["data"]
    assert body["total"] == 3
    assert body["uncategorized"] == 3


async def test_delete_category(client, real_note_service):
    resp = await client.post("/note/create", json=_note_payload(category="project"), headers={"Authorization": "Bearer x"})
    resp = await client.delete("/note/category/project", headers={"Authorization": "Bearer x"})
    body = resp.json()
    assert body["data"]["deleted_count"] == 1
    assert "成功删除分类" in body["message"]


# ---------------------------------------------------------------------------
# 单篇 CRUD
# ---------------------------------------------------------------------------
async def test_get_update_toggle_pin_delete_note(client, real_note_service):
    create_resp = await client.post("/note/create", json=_note_payload(), headers={"Authorization": "Bearer x"})
    note_id = create_resp.json()["data"]["id"]

    # 详情
    resp = await client.get(f"/note/{note_id}", headers={"Authorization": "Bearer x"})
    assert resp.json()["data"]["title"] == "测试笔记"

    # 更新
    resp = await client.put(f"/note/{note_id}", json={"title": "新标题", "content": "新内容"},
                            headers={"Authorization": "Bearer x"})
    body = resp.json()
    assert body["message"] == "笔记更新成功"
    assert body["data"]["title"] == "新标题"

    # 置顶切换
    resp = await client.put(f"/note/{note_id}/pin", headers={"Authorization": "Bearer x"})
    assert resp.json()["data"]["is_pinned"] is True

    # 删除
    resp = await client.delete(f"/note/{note_id}", headers={"Authorization": "Bearer x"})
    assert resp.json()["message"] == "笔记删除成功"

    # 删除后不存在
    resp = await client.get(f"/note/{note_id}", headers={"Authorization": "Bearer x"})
    assert resp.json()["message"] == "笔记不存在"


async def test_get_missing_note(client, real_note_service):
    resp = await client.get("/note/no-such-id", headers={"Authorization": "Bearer x"})
    assert resp.status_code == 200
    assert resp.json()["message"] == "笔记不存在"


# ---------------------------------------------------------------------------
# AI 能力
# ---------------------------------------------------------------------------
async def test_autocomplete(client, real_note_service):
    resp = await client.post("/note/autocomplete", json={"context": "今天天气"},
                             headers={"Authorization": "Bearer x"})
    body = resp.json()["data"]
    assert body["success"] is True
    assert body["completion"]


async def test_autocomplete_failure(client, real_note_service, monkeypatch):
    from app.core.background_init import init_manager

    class Boom:
        async def ainvoke(self, *a, **k):
            raise RuntimeError("llm down")

    monkeypatch.setattr(init_manager, "chat_model", Boom())
    resp = await client.post("/note/autocomplete", json={"context": "x"}, headers={"Authorization": "Bearer x"})
    body = resp.json()["data"]
    assert body["success"] is False
    assert body["completion"] == ""


async def test_assist_stream(client, real_note_service):
    async with client.stream(
        "POST", "/note/assist/stream",
        json={"content": "你好", "action": "expand"},
        headers={"Authorization": "Bearer x"},
    ) as resp:
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
        lines = [line async for line in resp.aiter_lines()]
    data_frames = [l for l in lines if l.startswith("data: ")]
    assert data_frames
    assert data_frames[-1] == "data: [DONE]"


async def test_auto_tag_endpoint(client, real_note_service):
    create_resp = await client.post("/note/create", json=_note_payload(), headers={"Authorization": "Bearer x"})
    note_id = create_resp.json()["data"]["id"]
    resp = await client.post(f"/note/{note_id}/auto-tag", headers={"Authorization": "Bearer x"})
    assert resp.status_code == 200
    assert resp.json()["message"] == "标签生成任务已提交"


async def test_related_notes(client, real_note_service, session_factory):
    from langchain_core.documents import Document
    from app.core.background_init import init_manager

    create_resp = await client.post("/note/create", json=_note_payload(), headers={"Authorization": "Bearer x"})
    note_id = create_resp.json()["data"]["id"]

    # 向向量层追加一篇“相似笔记”，供关联推荐检索
    init_manager.note_service.notes_store.add_documents(
        [Document(page_content="相似内容", metadata={
            "user_id": TEST_USER_ID, "note_id": "other-note", "doc_type": "note", "title": "相似笔记"})],
        ids=["other-note"],
    )
    async with session_factory() as s:
        from app.models.note import Note
        s.add(Note(id="other-note", user_id=TEST_USER_ID, title="相似笔记", content="相似内容"))
        await s.commit()

    resp = await client.get(f"/note/{note_id}/related", headers={"Authorization": "Bearer x"})
    items = resp.json()["data"]
    assert len(items) >= 1
    # 自身被排除，相似笔记保留
    assert all(item["id"] != note_id for item in items)
    assert items[0]["source"] == "note"
    assert items[0]["id"] == "other-note"


async def test_export_and_download(client, real_note_service):
    create_resp = await client.post("/note/create", json=_note_payload(), headers={"Authorization": "Bearer x"})
    note_id = create_resp.json()["data"]["id"]

    resp = await client.get(f"/note/{note_id}/export", headers={"Authorization": "Bearer x"})
    body = resp.json()["data"]
    assert body["filename"] == f"{note_id}.md"
    assert "title: 测试笔记" in body["markdown"]

    resp = await client.get(f"/note/{note_id}/download", headers={"Authorization": "Bearer x"})
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/markdown")
    assert "attachment" in resp.headers["content-disposition"]


async def test_batch_operations_require_auth(raw_client):
    resp = await raw_client.get("/note/list")
    assert resp.status_code == 401