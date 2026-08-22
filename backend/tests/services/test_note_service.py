"""NoteService 服务层测试 —— 真实业务逻辑 + FakeChromaStore + SQLite 内存库。

策略：
- 通过 conftest 的 real_note_service fixture 注入真实 NoteService（Chroma 已被替换为 FakeChromaStore）。
- `_auto_tag_and_review` 后台任务内部 `from app.db.db_config import AsyncSessionLocal` 为惰性导入，
  运行前用 patch_session_factory 把它指向 SQLite 工厂，并给 init_manager 装假 chat_model。
- 知识库检索分支用 install_fake_vector_store 替换 app.rag.vector_store.VectorStoreService。
"""
import asyncio
import json
import time
import uuid
import zipfile
from datetime import datetime, timedelta

import pytest
from langchain_core.documents import Document
from sqlalchemy import select

from app.models.note import Note
from app.models.review_record import ReviewRecord
from app.schemas.models import NoteCreate, NoteUpdate
from app.services.note_service import NoteService

from tests.conftest import (
    install_fake_vector_store,
    install_init_manager_fakes,
    patch_session_factory,
)
from tests.fakes import make_fake_chat_model

USER = "note-test-user"


def _uid(prefix: str = "note") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


async def _seed_note(db, user_id, *, title="标题", content="内容", category=None, tags=None, is_pinned=False, created_at=None, updated_at=None):
    note = Note(
        id=_uid(),
        user_id=user_id,
        title=title,
        content=content,
        category=category,
        tags=tags,
        is_pinned=is_pinned,
    )
    if created_at is not None:
        note.created_at = created_at
    db.add(note)
    await db.commit()
    await db.refresh(note)
    if updated_at is not None:
        note.updated_at = updated_at
        await db.commit()
        await db.refresh(note)
    return note


async def _pump(seconds: float = 0.2):
    """让后台 asyncio 任务有机会完成。"""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        await asyncio.sleep(0.01)


async def _fetch_note(session_factory, note_id: str) -> Note:
    async with session_factory() as s:
        return (await s.execute(select(Note).where(Note.id == note_id))).scalar_one()


def _install_tag_chat(monkeypatch, tags=("ai",), category="work"):
    payload = json.dumps({"tags": list(tags), "category": category}, ensure_ascii=False)
    return install_init_manager_fakes(monkeypatch, chat_model=make_fake_chat_model(responses=[payload]))


class _RaisingChatModel:
    async def ainvoke(self, messages):
        raise RuntimeError("fake model exploded")

    async def astream(self, messages):
        raise RuntimeError("fake model exploded")
        yield  # pragma: no cover


# ---------------------------------------------------------------------------
# create_note
# ---------------------------------------------------------------------------
async def test_create_note_commits_row_and_writes_vector(real_note_service, db_session, monkeypatch, session_factory):
    svc = real_note_service
    patch_session_factory(monkeypatch, session_factory)
    _install_tag_chat(monkeypatch)

    payload = NoteCreate(title="My Note", content="这是一段用于向量化的内容")
    response = await svc.create_note(db_session, USER, payload)

    assert response.user_id == USER
    assert response.title == "My Note"
    assert response.content == "这是一段用于向量化的内容"
    assert response.tags is None  # 用户未提供 → 后台任务稍后写入
    assert response.category is None

    # 向量层已写入
    got = svc.notes_store.get(where={"note_id": response.id})
    assert got["ids"] == [response.id]
    assert got["documents"] == ["这是一段用于向量化的内容"]

    await _pump()

    # 后台自动标签已完成并创建回顾记录
    row = await _fetch_note(session_factory, response.id)
    assert row.tags == ["ai"]
    assert row.category == "work"
    async with session_factory() as s:
        records = (await s.execute(select(ReviewRecord).where(ReviewRecord.note_id == response.id))).scalars().all()
        assert len(records) == 1
        rec = records[0]
        assert rec.review_count == 0
        assert rec.interval_days == 1
        assert rec.next_review_at > datetime.now() - timedelta(hours=1)


async def test_create_note_with_user_meta_skips_background_task(real_note_service, db_session, monkeypatch, session_factory):
    svc = real_note_service
    patch_session_factory(monkeypatch, session_factory)
    _install_tag_chat(monkeypatch)

    payload = NoteCreate(title="手动标签", content="内容", tags=["手动"], category="study")
    response = await svc.create_note(db_session, USER, payload)

    assert response.tags == ["手动"]
    assert response.category == "study"

    await _pump()
    async with session_factory() as s:
        records = (await s.execute(select(ReviewRecord).where(ReviewRecord.note_id == response.id))).scalars().all()
        assert records == []


# ---------------------------------------------------------------------------
# update_note
# ---------------------------------------------------------------------------
async def test_update_note_not_found_returns_none(real_note_service, db_session):
    svc = real_note_service
    result = await svc.update_note(db_session, _uid("missing"), USER, NoteUpdate(title="新标题"))
    assert result is None


async def test_update_note_content_change_deletes_and_re_adds_vector(real_note_service, db_session):
    svc = real_note_service
    created = await svc.create_note(db_session, USER, NoteCreate(title="旧标题", content="旧内容", tags=["t"], category="work"))

    updated = await svc.update_note(db_session, created.id, USER, NoteUpdate(title="新标题", content="新内容"))
    assert updated.title == "新标题"
    assert updated.content == "新内容"

    got = svc.notes_store.get(where={"note_id": created.id})
    assert got["ids"] == [created.id]
    assert got["documents"] == ["新内容"]
    assert got["metadatas"][0]["title"] == "新标题"


async def test_update_note_title_only_keeps_vector_untouched(real_note_service, db_session):
    svc = real_note_service
    created = await svc.create_note(db_session, USER, NoteCreate(title="旧标题", content="正文内容", tags=["t"], category="work"))

    await svc.update_note(db_session, created.id, USER, NoteUpdate(title="新标题"))

    got = svc.notes_store.get(where={"note_id": created.id})
    assert got["documents"] == ["正文内容"]  # content 未变，向量未重写


# ---------------------------------------------------------------------------
# get_note
# ---------------------------------------------------------------------------
async def test_get_note_found_missing_and_other_user(real_note_service, db_session):
    svc = real_note_service
    note = await _seed_note(db_session, USER, title="可见笔记")

    found = await svc.get_note(db_session, note.id, USER)
    assert found is not None
    assert found.title == "可见笔记"

    assert await svc.get_note(db_session, _uid("missing"), USER) is None
    assert await svc.get_note(db_session, note.id, "other-user") is None


# ---------------------------------------------------------------------------
# list_notes
# ---------------------------------------------------------------------------
async def test_list_notes_pagination_and_total(real_note_service, db_session):
    svc = real_note_service
    for i in range(5):
        await _seed_note(db_session, USER, title=f"笔记{i}")

    page1, total = await svc.list_notes(db_session, USER, page=1, page_size=2)
    assert len(page1) == 2
    assert total == 5
    page3, _ = await svc.list_notes(db_session, USER, page=3, page_size=2)
    assert len(page3) == 1


async def test_list_notes_category_filter(real_note_service, db_session):
    svc = real_note_service
    await _seed_note(db_session, USER, title="工作笔记", category="work")
    await _seed_note(db_session, USER, title="学习笔记", category="study")

    work_notes, total = await svc.list_notes(db_session, USER, category="work")
    assert total == 1
    assert [n.title for n in work_notes] == ["工作笔记"]


async def test_list_notes_tag_filter_in_memory(real_note_service, db_session):
    svc = real_note_service
    await _seed_note(db_session, USER, title="AI 笔记", tags=["ai"])
    await _seed_note(db_session, USER, title="ML 笔记", tags=["ml"])
    await _seed_note(db_session, USER, title="无标签笔记", tags=[])

    tagged, total = await svc.list_notes(db_session, USER, tag="ai")
    assert [n.title for n in tagged] == ["AI 笔记"]
    assert total == 3  # tag 过滤不改变 total（内存过滤）


async def test_list_notes_sort_by_created_at_desc(real_note_service, db_session):
    svc = real_note_service
    base = datetime.now() - timedelta(days=10)
    await _seed_note(db_session, USER, title="最旧", created_at=base)
    await _seed_note(db_session, USER, title="中间", created_at=base + timedelta(days=1))
    await _seed_note(db_session, USER, title="最新", created_at=base + timedelta(days=2))

    notes, _ = await svc.list_notes(db_session, USER, sort_by="created_at")
    assert [n.title for n in notes] == ["最新", "中间", "最旧"]


async def test_list_notes_sort_by_title_asc(real_note_service, db_session):
    svc = real_note_service
    await _seed_note(db_session, USER, title="banana")
    await _seed_note(db_session, USER, title="apple")
    await _seed_note(db_session, USER, title="cherry")

    notes, _ = await svc.list_notes(db_session, USER, sort_by="title")
    assert [n.title for n in notes] == ["apple", "banana", "cherry"]


async def test_list_notes_pinned_first(real_note_service, db_session):
    svc = real_note_service
    base = datetime.now() - timedelta(days=1)
    pinned = await _seed_note(db_session, USER, title="置顶旧笔记", is_pinned=True, updated_at=base)
    recent = await _seed_note(db_session, USER, title="未置顶新笔记", updated_at=base + timedelta(hours=12))

    notes, _ = await svc.list_notes(db_session, USER)
    assert [n.id for n in notes] == [pinned.id, recent.id]


# ---------------------------------------------------------------------------
# search_notes
# ---------------------------------------------------------------------------
async def test_search_notes_returns_mysql_backfill_in_store_order(real_note_service, db_session, monkeypatch, session_factory):
    svc = real_note_service
    patch_session_factory(monkeypatch, session_factory)
    n1 = await _seed_note(db_session, USER, title="第一", content="vector target")
    n2 = await _seed_note(db_session, USER, title="第二", content="another target")
    ghost_id = _uid("ghost")

    # 向量层预置命中：顺序故意与 ID 顺序相反，验证按向量顺序回填
    svc.notes_store.add_documents(
        [
            Document(page_content="matches", metadata={"user_id": USER, "note_id": n2.id, "doc_type": "note", "title": "第二"}),
            Document(page_content="matches", metadata={"user_id": USER, "note_id": n1.id, "doc_type": "note", "title": "第一"}),
            Document(page_content="matches", metadata={"user_id": USER, "note_id": ghost_id, "doc_type": "note", "title": "幽灵"}),
        ],
        ids=[n2.id, n1.id, ghost_id],
    )

    results = await svc.search_notes(db_session, USER, "query", top_k=10)
    assert [r.id for r in results] == [n2.id, n1.id]  # 幽灵笔记不在 MySQL 中 → 跳过


async def test_search_notes_returns_empty_when_store_raises(real_note_service, db_session, monkeypatch):
    svc = real_note_service

    def _boom(*args, **kwargs):
        raise RuntimeError("vector store down")

    monkeypatch.setattr(svc.notes_store, "similarity_search", _boom)
    assert await svc.search_notes(db_session, USER, "query") == []


# ---------------------------------------------------------------------------
# delete_note
# ---------------------------------------------------------------------------
async def test_delete_note_missing_returns_false(real_note_service, db_session):
    svc = real_note_service
    assert await svc.delete_note(db_session, _uid("missing"), USER) is False


async def test_delete_note_removes_row_and_vector(real_note_service, db_session, session_factory):
    svc = real_note_service
    created = await svc.create_note(db_session, USER, NoteCreate(title="待删", content="内容", tags=["t"], category="work"))

    assert await svc.delete_note(db_session, created.id, USER) is True

    async with session_factory() as s:
        row = (await s.execute(select(Note).where(Note.id == created.id))).scalar_one_or_none()
        assert row is None
    assert svc.notes_store.get(where={"note_id": created.id})["ids"] == []


# ---------------------------------------------------------------------------
# get_category_stats / delete_category
# ---------------------------------------------------------------------------
async def test_get_category_stats(real_note_service, db_session):
    svc = real_note_service
    await _seed_note(db_session, USER, title="w1", category="work")
    await _seed_note(db_session, USER, title="w2", category="work")
    await _seed_note(db_session, USER, title="s1", category="study")
    await _seed_note(db_session, USER, title="无分类")

    stats = await svc.get_category_stats(db_session, USER)
    assert stats["total"] == 4
    assert stats["uncategorized"] == 1
    assert {c["category"]: c["count"] for c in stats["categories"]} == {"work": 2, "study": 1}


async def test_delete_category_removes_notes_and_vectors(real_note_service, db_session, session_factory):
    svc = real_note_service
    w1 = await _seed_note(db_session, USER, title="w1", category="work")
    w2 = await _seed_note(db_session, USER, title="w2", category="work")
    s1 = await _seed_note(db_session, USER, title="s1", category="study")

    deleted = await svc.delete_category(db_session, USER, "work")
    assert deleted == 2

    async with session_factory() as s:
        remaining = (await s.execute(select(Note.id).where(Note.user_id == USER))).scalars().all()
        assert remaining == [s1.id]
    assert svc.notes_store.get(where={"note_id": w1.id})["ids"] == []
    assert svc.notes_store.get(where={"note_id": w2.id})["ids"] == []


async def test_delete_category_missing_returns_zero(real_note_service, db_session):
    svc = real_note_service
    assert await svc.delete_category(db_session, USER, "not-a-category") == 0


# ---------------------------------------------------------------------------
# batch operations
# ---------------------------------------------------------------------------
async def test_batch_delete_notes(real_note_service, db_session, session_factory):
    svc = real_note_service
    n1 = await _seed_note(db_session, USER, title="n1")
    n2 = await _seed_note(db_session, USER, title="n2")
    n3 = await _seed_note(db_session, "other-user", title="他人笔记")

    deleted = await svc.batch_delete_notes(db_session, USER, [n1.id, n2.id, n3.id])
    assert deleted == 2  # n3 不属于 USER，跳过

    async with session_factory() as s:
        remaining = (await s.execute(select(Note.id).where(Note.user_id == USER))).scalars().all()
        assert remaining == []


async def test_batch_delete_notes_empty_ids(real_note_service, db_session):
    svc = real_note_service
    assert await svc.batch_delete_notes(db_session, USER, []) == 0


async def test_batch_update_category(real_note_service, db_session, session_factory):
    svc = real_note_service
    n1 = await _seed_note(db_session, USER, title="n1", category="work")
    n2 = await _seed_note(db_session, USER, title="n2", category="work")
    other = await _seed_note(db_session, "other-user", title="o")

    updated = await svc.batch_update_category(db_session, USER, [n1.id, n2.id, other.id], "life")
    assert updated == 2

    async with session_factory() as s:
        cats = (await s.execute(select(Note.category).where(Note.user_id == USER))).scalars().all()
        assert cats == ["life", "life"]


async def test_batch_update_pin(real_note_service, db_session, session_factory):
    svc = real_note_service
    n1 = await _seed_note(db_session, USER, title="n1")
    n2 = await _seed_note(db_session, USER, title="n2")

    updated = await svc.batch_update_pin(db_session, USER, [n1.id, n2.id], True)
    assert updated == 2

    async with session_factory() as s:
        pinned = (await s.execute(select(Note.id).where(Note.user_id == USER, Note.is_pinned.is_(True)))).scalars().all()
        assert sorted(pinned) == sorted([n1.id, n2.id])


# ---------------------------------------------------------------------------
# export
# ---------------------------------------------------------------------------
async def test_export_note_markdown_frontmatter(real_note_service, db_session):
    svc = real_note_service
    note = await _seed_note(db_session, USER, title="导出笔记", content="正文内容", category="work", tags=["ai", "测试"])

    md = await svc.export_note_markdown(db_session, note.id, USER)
    assert md is not None
    lines = md.split("\n")
    assert lines[0] == "---"
    assert any(line == "title: 导出笔记" for line in lines)
    assert any(line == "tags: [ai, 测试]" for line in lines)
    assert any(line == "category: work" for line in lines)
    assert any(line.startswith("created_at: ") for line in lines)
    assert any(line.startswith("updated_at: ") for line in lines)
    assert "---" in lines
    assert "# 导出笔记" in lines
    assert "正文内容" in lines


async def test_export_note_markdown_not_found(real_note_service, db_session):
    svc = real_note_service
    assert await svc.export_note_markdown(db_session, _uid("missing"), USER) is None


async def test_batch_export_zip_contains_md_entries(real_note_service, db_session):
    svc = real_note_service
    n1 = await _seed_note(db_session, USER, title="alpha", content="alpha 内容", category="work")
    n2 = await _seed_note(db_session, USER, title="beta", content="beta 内容", category="study")

    blob = await svc.batch_export_zip(db_session, USER, [n1.id, n2.id])

    assert isinstance(blob, bytes) and len(blob) > 0
    with zipfile.ZipFile(__import__("io").BytesIO(blob)) as zf:
        names = zf.namelist()
        assert sorted(names) == ["alpha.md", "beta.md"]
        content = zf.read("alpha.md").decode("utf-8")
        assert "title: alpha" in content
        assert "alpha 内容" in content


async def test_batch_export_zip_skips_missing(real_note_service, db_session):
    svc = real_note_service
    blob = await svc.batch_export_zip(db_session, USER, [_uid("missing")])
    with zipfile.ZipFile(__import__("io").BytesIO(blob)) as zf:
        assert zf.namelist() == []


# ---------------------------------------------------------------------------
# autocomplete / assist_stream
# ---------------------------------------------------------------------------
async def test_autocomplete_success(monkeypatch):
    install_init_manager_fakes(monkeypatch, chat_model=make_fake_chat_model(responses=["续写的内容"]))
    svc = NoteService.__new__(NoteService)  # 不触发 Chroma 初始化

    result = await svc.autocomplete("上下文文本")
    assert result == {"success": True, "completion": "续写的内容"}


async def test_autocomplete_failure_returns_success_false(monkeypatch):
    install_init_manager_fakes(monkeypatch, chat_model=_RaisingChatModel())
    svc = NoteService.__new__(NoteService)

    result = await svc.autocomplete("上下文")
    assert result == {"success": False, "completion": ""}


async def test_assist_stream_yields_sse_frames(monkeypatch):
    response_text = "今天天气不错。"
    install_init_manager_fakes(monkeypatch, chat_model=make_fake_chat_model(responses=[response_text]))
    svc = NoteService.__new__(NoteService)

    frames = []
    async for frame in svc.assist_stream("选中的文本", "expand"):
        frames.append(frame)

    assert len(frames) >= 2
    assert frames[-1] == "data: [DONE]\n\n"
    assert all(f.startswith("data: ") for f in frames[:-1])
    payload = "".join(f[len("data: "):-2] for f in frames[:-1])  # 去掉 "data: " 和末尾 "\n\n"
    assert payload == response_text


# ---------------------------------------------------------------------------
# get_related_notes
# ---------------------------------------------------------------------------
async def test_get_related_notes_merges_note_and_kb_sources(real_note_service, db_session, monkeypatch):
    svc = real_note_service
    install_fake_vector_store(monkeypatch)  # 替换知识库 VectorStoreService 为内存替身

    # 预置知识库文档到替身的 vectors_store
    from app.rag.vector_store import VectorStoreService as PatchedVS

    kb_svc = PatchedVS()  # 触发 factory → 缓存实例
    kb_svc.vectors_store.add_documents(
        [Document(page_content="知识库切片内容", metadata={"source": "kb_doc.py", "original_filename": "kb_doc.py", "user_id": USER})],
        ids=["kb1"],
    )

    note_a = await svc.create_note(db_session, USER, NoteCreate(title="主笔记", content="主内容", tags=["t"], category="work"))
    note_b = await svc.create_note(db_session, USER, NoteCreate(title="相似笔记", content="相似内容", tags=["t"], category="work"))

    related = await svc.get_related_notes(db_session, note_a.id, USER, top_k=3)

    ids = [r["id"] for r in related]
    assert note_a.id not in ids  # 排除自身
    assert note_b.id in ids
    sources = {r["source"] for r in related}
    assert sources == {"note", "knowledge_base"}
    by_id = {r["id"]: r for r in related}
    assert by_id[note_b.id]["title"] == "相似笔记"
    assert by_id[note_b.id]["similarity"] == 0.5
    assert by_id["kb_doc.py"]["title"] == "kb_doc.py"
    assert by_id["kb_doc.py"]["source"] == "knowledge_base"
    # 相似度升序排列
    assert [r["similarity"] for r in related] == sorted(r["similarity"] for r in related)


async def test_get_related_notes_missing_note_returns_empty(real_note_service, db_session):
    svc = real_note_service
    assert await svc.get_related_notes(db_session, _uid("missing"), USER) == []


async def test_get_related_notes_respects_top_k(real_note_service, db_session, monkeypatch):
    svc = real_note_service
    install_fake_vector_store(monkeypatch)
    from app.rag.vector_store import VectorStoreService as PatchedVS

    kb_svc = PatchedVS()
    kb_svc.vectors_store.add_documents(
        [Document(page_content="kb", metadata={"source": "kb.py", "original_filename": "kb.py", "user_id": USER})],
        ids=["kb1"],
    )
    note_a = await svc.create_note(db_session, USER, NoteCreate(title="A", content="A内容", tags=["t"], category="work"))
    note_b = await svc.create_note(db_session, USER, NoteCreate(title="B", content="B内容", tags=["t"], category="work"))

    related = await svc.get_related_notes(db_session, note_a.id, USER, top_k=1)
    assert len(related) == 1


# ---------------------------------------------------------------------------
# _extract_json
# ---------------------------------------------------------------------------
def test_extract_json_from_markdown_block():
    text = '好的：\n```json\n{"tags": ["ai"], "category": "work"}\n```\n结束'
    assert NoteService._extract_json(text) == '{"tags": ["ai"], "category": "work"}'


def test_extract_json_bare_object_with_surrounding_text():
    text = '结果是 {"question": "q", "choices": ["a"]} 完'
    assert NoteService._extract_json(text) == '{"question": "q", "choices": ["a"]}'


def test_extract_json_plain_object():
    assert NoteService._extract_json('{"a": 1}') == '{"a": 1}'


def test_extract_json_no_json_returns_input():
    text = "这里没有任何 JSON"
    assert NoteService._extract_json(text) == text