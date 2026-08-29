import asyncio

import pytest
from sqlalchemy import select

from app.graph.services.graph_service import content_hash
from app.models.graph import GraphEntityNote, GraphExtractLog, GraphNoteEdge
from app.models.note import Note
from app.schemas.models import NoteCreate, NoteUpdate
from tests.fakes import make_fake_chat_model


@pytest.mark.asyncio
async def test_create_note_triggers_extraction_and_delete_cleans(db_session, session_factory,
                                                                 real_note_service, monkeypatch):
    from app.core.background_init import init_manager
    monkeypatch.setattr(init_manager, "chat_model",
                        make_fake_chat_model(['{"entities": [{"name": "Python", "mentions": ["Python"]}], "relations": []}']))
    monkeypatch.setattr("app.graph.services.graph_service.AsyncSessionLocal", session_factory)

    note = await real_note_service.create_note(db_session, "u1", NoteCreate(title="t", content="用 Python 写"))
    note_id = note.id
    await asyncio.sleep(0.05)  # note_service 内 create_task 触发的入队先落地
    from app.graph.services.graph_worker import _tick
    assert await _tick() is True  # worker 消费构建任务
    log = (await db_session.execute(select(GraphExtractLog).where(
        GraphExtractLog.note_id == note_id))).scalar_one()
    assert log.status == "success"
    en = (await db_session.execute(select(GraphEntityNote).where(
        GraphEntityNote.note_id == note_id))).scalars().all()
    assert any(r.entity_id for r in en)

    # 删除 → 清理
    from app.graph.services.graph_service import cleanup_note_graph
    await cleanup_note_graph(db_session, "u1", note_id)
    await db_session.commit()
    assert (await db_session.execute(select(GraphExtractLog).where(
        GraphExtractLog.note_id == note_id))).scalars().all() == []