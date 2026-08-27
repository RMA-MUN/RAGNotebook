import asyncio

import pytest
from sqlalchemy import select

from app.graph.services.graph_service import (
    _run_extraction,
    cleanup_doc_graph,
    cleanup_note_graph,
    content_hash,
    maybe_schedule_doc_extraction,
    maybe_schedule_extraction,
)
from app.models.graph import (
    GraphDoc,
    GraphEntity,
    GraphEntityNote,
    GraphExtractLog,
    GraphNoteEdge,
)
from app.models.note import Note
from tests.fakes import make_fake_chat_model


def test_content_hash_stable():
    assert content_hash("hello") == content_hash("hello")
    assert content_hash("hello") != content_hash("world")


@pytest.mark.asyncio
async def test_maybe_schedule_extraction_triggers_once(db_session, session_factory, monkeypatch):
    from app.core.background_init import init_manager
    monkeypatch.setattr(init_manager, "chat_model",
                        make_fake_chat_model(['{"entities": [], "relations": []}']))
    monkeypatch.setattr("app.graph.services.graph_service.AsyncSessionLocal", session_factory)

    db_session.add(GraphExtractLog(id="log1", user_id="u1", note_id="n1",
                                   content_hash=content_hash("v1"), status="success"))
    await db_session.commit()

    # hash 相同 → 不触发
    triggered = await maybe_schedule_extraction("n1", "u1", "标题", "v1")
    assert triggered is False

    # hash 不同 → 触发
    triggered2 = await maybe_schedule_extraction("n1", "u1", "标题", "v2")
    assert triggered2 is True

    # 等后台任务完成（fire-and-forget）
    await asyncio.sleep(0.2)
    log = (await db_session.execute(
        select(GraphExtractLog).where(GraphExtractLog.note_id == "n1"))).scalar_one()
    assert log.content_hash == content_hash("v2")
    assert log.status == "success"


@pytest.mark.asyncio
async def test_run_extraction_writes_entities_relations_and_edges(db_session, session_factory, monkeypatch):
    from app.core.background_init import init_manager
    monkeypatch.setattr(init_manager, "chat_model",
                        make_fake_chat_model(['{"entities": [{"name": "Python", "mentions": ["Python"]}], '
                                               '"relations": []}']))
    monkeypatch.setattr("app.graph.services.graph_service.AsyncSessionLocal", session_factory)
    db_session.add(GraphExtractLog(id="log2", user_id="u1", note_id="n1", content_hash="h",
                                   status="pending"))
    # 双链目标笔记（[[FastAPI]] 需要命中已存在的笔记才会生成 wiki 边）
    db_session.add(Note(id="n2", user_id="u1", title="FastAPI", content="FastAPI 文档"))
    await db_session.commit()

    await _run_extraction("n1", "u1", "我的笔记", content_hash("body"), body="用 [[FastAPI]] 与 Python")

    # 双链边
    edges = (await db_session.execute(
        select(GraphNoteEdge).where(GraphNoteEdge.user_id == "u1"))).scalars().all()
    assert any(e.kind == "wiki" for e in edges)
    # 实体与实体-笔记关联
    entities = (await db_session.execute(
        select(GraphEntity).where(GraphEntity.user_id == "u1"))).scalars().all()
    assert any(e.name == "Python" for e in entities)


@pytest.mark.asyncio
async def test_duplicate_title_notes_uses_first_match(db_session, session_factory, monkeypatch):
    from app.core.background_init import init_manager
    monkeypatch.setattr(init_manager, "chat_model",
                        make_fake_chat_model(['{"entities": [], "relations": []}']))
    monkeypatch.setattr("app.graph.services.graph_service.AsyncSessionLocal", session_factory)
    db_session.add(GraphExtractLog(id="log3", user_id="u1", note_id="n1", content_hash="h",
                                   status="pending"))
    # 重名笔记：Note.title 无唯一约束，首条匹配生效，不得 MultipleResultsFound
    db_session.add(Note(id="n2", user_id="u1", title="FastAPI", content="a"))
    db_session.add(Note(id="n3", user_id="u1", title="FastAPI", content="b"))
    await db_session.commit()

    await _run_extraction("n1", "u1", "我的笔记", content_hash("body"), body="[[FastAPI]]")

    edges = (await db_session.execute(select(GraphNoteEdge).where(
        GraphNoteEdge.user_id == "u1", GraphNoteEdge.source_note_id == "n1"))).scalars().all()
    assert len(edges) == 1
    assert edges[0].target_note_id == "n2"
    log = (await db_session.execute(select(GraphExtractLog).where(
        GraphExtractLog.note_id == "n1"))).scalar_one()
    assert log.status == "success"


@pytest.mark.asyncio
async def test_re_extract_removes_stale_reverse_edges(db_session, session_factory, monkeypatch):
    from app.core.background_init import init_manager
    monkeypatch.setattr(init_manager, "chat_model",
                        make_fake_chat_model(['{"entities": [], "relations": []}']))
    monkeypatch.setattr("app.graph.services.graph_service.AsyncSessionLocal", session_factory)
    db_session.add(GraphExtractLog(id="log4", user_id="u1", note_id="n1", content_hash="h",
                                   status="pending"))
    db_session.add(Note(id="n2", user_id="u1", title="FastAPI", content="FastAPI 文档"))
    await db_session.commit()

    # 首抽：[[FastAPI]] → n1→n2 出边 + n2→n1 反向边
    await _run_extraction("n1", "u1", "我的笔记", content_hash("v1"), body="[[FastAPI]]")
    reverse = (await db_session.execute(select(GraphNoteEdge).where(
        GraphNoteEdge.user_id == "u1", GraphNoteEdge.source_note_id == "n2",
        GraphNoteEdge.target_note_id == "n1"))).scalar_one_or_none()
    assert reverse is not None

    # 重抽：内容不再含 [[FastAPI]] → 出边与过期反向边都应被清理
    await _run_extraction("n1", "u1", "我的笔记", content_hash("v2"), body="没有链接了")
    edges = (await db_session.execute(select(GraphNoteEdge).where(
        GraphNoteEdge.user_id == "u1"))).scalars().all()
    assert edges == []


@pytest.mark.asyncio
async def test_cleanup_note_graph_removes_all_relations(db_session):
    from sqlalchemy import insert
    await db_session.execute(insert(GraphNoteEdge).values(id="w1", user_id="u1",
                                                          source_note_id="n1", target_note_id="n2",
                                                          kind="wiki"))
    await db_session.execute(insert(GraphEntityNote).values(id="en1", user_id="u1",
                                                            entity_id="e1", note_id="n1"))
    await db_session.execute(insert(GraphExtractLog).values(id="g1", user_id="u1", note_id="n1",
                                                            content_hash="h", status="success"))
    await db_session.commit()

    await cleanup_note_graph(db_session, "u1", "n1")
    assert (await db_session.execute(
        select(GraphNoteEdge).where(GraphNoteEdge.user_id == "u1"))).scalars().all() == []
    assert (await db_session.execute(
        select(GraphEntityNote).where(GraphEntityNote.user_id == "u1"))).scalars().all() == []
    assert (await db_session.execute(
        select(GraphExtractLog).where(GraphExtractLog.user_id == "u1"))).scalars().all() == []


@pytest.mark.asyncio
async def test_maybe_schedule_doc_extraction_creates_doc_and_triggers(db_session, session_factory, monkeypatch):
    from app.core.background_init import init_manager
    monkeypatch.setattr(init_manager, "chat_model",
                        make_fake_chat_model(['{"entities": [{"name": "Python", "mentions": ["Python"]}], "relations": []}']))
    monkeypatch.setattr("app.graph.services.graph_service.AsyncSessionLocal", session_factory)

    triggered = await maybe_schedule_doc_extraction("u1", "md51", "报告.pdf", "用 Python 写的报告")
    assert triggered is True

    await asyncio.sleep(0.2)
    doc = (await db_session.execute(select(GraphDoc).where(GraphDoc.id == "md51"))).scalar_one()
    assert doc.filename == "报告.pdf"
    log = (await db_session.execute(select(GraphExtractLog).where(
        GraphExtractLog.note_id == "md51", GraphExtractLog.source_type == "doc"))).scalar_one()
    assert log.status == "success"
    en = (await db_session.execute(select(GraphEntityNote).where(
        GraphEntityNote.note_id == "md51", GraphEntityNote.source_type == "doc"))).scalars().all()
    assert any(r.entity_id for r in en)


@pytest.mark.asyncio
async def test_maybe_schedule_doc_extraction_skips_same_hash(db_session, session_factory, monkeypatch):
    monkeypatch.setattr("app.graph.services.graph_service.AsyncSessionLocal", session_factory)
    db_session.add(GraphExtractLog(id="logd", user_id="u1", note_id="md51",
                                   content_hash=content_hash("v1"), status="success", source_type="doc"))
    await db_session.commit()

    triggered = await maybe_schedule_doc_extraction("u1", "md51", "报告.pdf", "v1")
    assert triggered is False
    # 即便跳过抽取，文档节点元数据也必须落库（否则总览图看不到文档节点）
    doc = (await db_session.execute(select(GraphDoc).where(GraphDoc.id == "md51"))).scalar_one_or_none()
    assert doc is not None and doc.filename == "报告.pdf"


@pytest.mark.asyncio
async def test_run_extraction_doc_skips_note_edges(db_session, session_factory, monkeypatch):
    from app.core.background_init import init_manager
    monkeypatch.setattr(init_manager, "chat_model",
                        make_fake_chat_model(['{"entities": [{"name": "Python", "mentions": ["Python"]}], "relations": []}']))
    monkeypatch.setattr("app.graph.services.graph_service.AsyncSessionLocal", session_factory)
    db_session.add(GraphDoc(id="md51", user_id="u1", filename="报告.pdf"))
    db_session.add(GraphExtractLog(id="logd2", user_id="u1", note_id="md51", content_hash="h",
                                   status="pending", source_type="doc"))
    await db_session.commit()

    # 文档正文里即便有 [[双链]] 语法也不生成笔记双链边
    await _run_extraction("md51", "u1", "报告.pdf", content_hash("body"),
                          body="用 [[FastAPI]] 与 Python", source_type="doc")

    assert (await db_session.execute(
        select(GraphNoteEdge).where(GraphNoteEdge.user_id == "u1"))).scalars().all() == []
    entities = (await db_session.execute(
        select(GraphEntity).where(GraphEntity.user_id == "u1"))).scalars().all()
    assert any(e.name == "Python" for e in entities)
    en = (await db_session.execute(select(GraphEntityNote).where(
        GraphEntityNote.user_id == "u1", GraphEntityNote.note_id == "md51"))).scalars().all()
    assert len(en) == 1 and en[0].source_type == "doc"
    log = (await db_session.execute(select(GraphExtractLog).where(
        GraphExtractLog.note_id == "md51"))).scalar_one()
    assert log.status == "success"


@pytest.mark.asyncio
async def test_doc_extraction_failure_marks_log_failed(db_session, session_factory, monkeypatch):
    from app.core.background_init import init_manager
    monkeypatch.setattr(init_manager, "chat_model",
                        make_fake_chat_model(['{"entities": [{"name": "Python", "mentions": ["Python"]}], "relations": []}']))
    monkeypatch.setattr("app.graph.services.graph_service.AsyncSessionLocal", session_factory)
    db_session.add(GraphDoc(id="md51", user_id="u1", filename="报告.pdf"))
    db_session.add(GraphExtractLog(id="logd3", user_id="u1", note_id="md51", content_hash="h",
                                   status="pending", source_type="doc"))
    await db_session.commit()

    async def _boom(*args, **kwargs):
        raise RuntimeError("LLM 不可用")
    monkeypatch.setattr("app.graph.services.graph_service.extract_entities", _boom)

    await _run_extraction("md51", "u1", "报告.pdf", content_hash("body"),
                          body="用 Python 写", source_type="doc")

    log = (await db_session.execute(select(GraphExtractLog).where(
        GraphExtractLog.note_id == "md51"))).scalar_one()
    assert log.status == "failed"


@pytest.mark.asyncio
async def test_cleanup_doc_graph_removes_doc_rows_keeps_entity(db_session, session_factory, monkeypatch):
    monkeypatch.setattr("app.graph.services.graph_service.AsyncSessionLocal", session_factory)
    from sqlalchemy import insert
    await db_session.execute(insert(GraphEntity).values(id="e1", user_id="u1",
                                                        name="Python", display_name="Python"))
    await db_session.execute(insert(GraphDoc).values(id="md51", user_id="u1", filename="报告.pdf"))
    await db_session.execute(insert(GraphEntityNote).values(id="end1", user_id="u1", entity_id="e1",
                                                            note_id="md51", source_type="doc"))
    await db_session.execute(insert(GraphExtractLog).values(id="gd1", user_id="u1", note_id="md51",
                                                            content_hash="h", status="success",
                                                            source_type="doc"))
    await db_session.commit()

    await cleanup_doc_graph("u1", "md51")
    assert (await db_session.execute(
        select(GraphDoc).where(GraphDoc.user_id == "u1"))).scalars().all() == []
    assert (await db_session.execute(
        select(GraphEntityNote).where(GraphEntityNote.user_id == "u1"))).scalars().all() == []
    assert (await db_session.execute(
        select(GraphExtractLog).where(GraphExtractLog.user_id == "u1"))).scalars().all() == []
    # 实体本身保留（与其他来源共享）
    assert (await db_session.execute(
        select(GraphEntity).where(GraphEntity.user_id == "u1"))).scalars().all()
