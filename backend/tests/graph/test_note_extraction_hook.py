"""笔记保存/删除的图谱抽取联动测试（需要真实 Neo4j）。

    NEO4J_TEST_URI=bolt://localhost:7687 uv run --extra dev pytest tests/graph/test_note_extraction_hook.py -v

创建笔记 → worker 消费 → 实体落 Neo4j；删除笔记 → 图谱与抽取日志同步清理。
"""
import asyncio
import os
import uuid

import pytest
from sqlalchemy import select

from app.models.graph import GraphExtractLog
from app.schemas.models import NoteCreate
from tests.fakes import make_fake_chat_model

pytestmark = pytest.mark.skipif(
    not os.getenv("NEO4J_TEST_URI"), reason="需要真实 Neo4j（设 NEO4J_TEST_URI 启用）")


@pytest.fixture
def neo4j_env(monkeypatch):
    from app.core.failed_response import settings

    monkeypatch.setattr(settings, "NEO4J_URI", os.environ["NEO4J_TEST_URI"], raising=False)


@pytest.mark.asyncio
async def test_create_note_triggers_extraction_and_delete_cleans(db_session, session_factory,
                                                                 real_note_service, monkeypatch,
                                                                 neo4j_env, _cleanup):
    uid = "pipe-" + uuid.uuid4().hex[:8]
    from app.core.background_init import init_manager
    monkeypatch.setattr(init_manager, "chat_model",
                        make_fake_chat_model(['{"entities": [{"name": "Python", "mentions": ["Python"]}], "relations": []}']))
    monkeypatch.setattr("app.graph.services.graph_service.AsyncSessionLocal", session_factory)

    note = await real_note_service.create_note(db_session, uid, NoteCreate(title="t", content="用 Python 写"))
    note_id = note.id
    await asyncio.sleep(0.05)  # note_service 内 create_task 触发的入队先落地
    from app.graph.services.graph_worker import _tick
    assert await _tick() is True  # worker 消费构建任务
    log = (await db_session.execute(select(GraphExtractLog).where(
        GraphExtractLog.note_id == note_id))).scalar_one()
    assert log.status == "success"

    # 实体落 Neo4j（MySQL 侧不再有 GraphEntityNote 行）
    from app.graph.storage import neo4j_client

    driver = neo4j_client.get_neo4j_driver()
    entity_cnt = await driver.execute_query(
        "MATCH (e:Entity {user_id: $uid, name: 'Python'}) RETURN count(e) AS c", {"uid": uid})
    assert entity_cnt.records[0]["c"] == 1

    # 删除 → 清理（Neo4j 实体 + MySQL 抽取日志）
    from app.graph.services.graph_service import cleanup_note_graph
    await cleanup_note_graph(db_session, uid, note_id)
    await db_session.commit()
    assert (await db_session.execute(select(GraphExtractLog).where(
        GraphExtractLog.note_id == note_id))).scalars().all() == []
    entity_cnt = await driver.execute_query(
        "MATCH (e:Entity {user_id: $uid, name: 'Python'}) RETURN count(e) AS c", {"uid": uid})
    assert entity_cnt.records[0]["c"] == 0
