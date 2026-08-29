"""图谱构建 worker 测试：入队幂等、认领互斥、失败重试、强制重抽。"""
import json

import pytest
from sqlalchemy import select

from app.graph.services import graph_service
from app.graph.services.graph_service import content_hash
from app.graph.services.graph_worker import _claim_next_task, _tick
from app.models.graph import GraphBuildTask, GraphDoc, GraphExtractLog
from app.models.note import Note
from tests.fakes import make_fake_chat_model


@pytest.fixture
def patched(session_factory, monkeypatch):
    monkeypatch.setattr("app.graph.services.graph_service.AsyncSessionLocal", session_factory)
    return session_factory


@pytest.mark.asyncio
async def test_enqueue_upsert_idempotent(db_session, patched):
    assert await graph_service._enqueue_build_task(
        "u1", "note", "n1", "标题", content_hash("v1"), {"text": "v1"}) is True
    assert await graph_service._enqueue_build_task(
        "u1", "note", "n1", "标题2", content_hash("v2"), {"text": "v2"}) is True

    rows = (await db_session.execute(
        select(GraphBuildTask).where(GraphBuildTask.user_id == "u1"))).scalars().all()
    assert len(rows) == 1
    assert rows[0].title == "标题2"
    assert rows[0].content_hash == content_hash("v2")
    assert json.loads(rows[0].payload) == {"text": "v2"}
    assert rows[0].status == "pending"


@pytest.mark.asyncio
async def test_enqueue_resets_attempts_on_content_change(db_session, patched):
    await graph_service._enqueue_build_task("u1", "doc", "md5", "f.pdf", "h1", {"text": "a"})
    async with patched() as db:
        row = (await db.execute(select(GraphBuildTask))).scalar_one()
        row.attempts = 2
        row.status = "failed"
        await db.commit()
    # 相同内容重入队：不重置计数、保留 failed → 也不复活
    await graph_service._enqueue_build_task("u1", "doc", "md5", "f.pdf", "h1", {"text": "a"})
    async with patched() as db:
        row = (await db.execute(select(GraphBuildTask))).scalar_one()
        assert row.attempts == 2
    # 内容变化：重置计数并回到 pending
    await graph_service._enqueue_build_task("u1", "doc", "md5", "f.pdf", "h2", {"text": "b"})
    async with patched() as db:
        row = (await db.execute(select(GraphBuildTask))).scalar_one()
        assert row.attempts == 0 and row.status == "pending"


@pytest.mark.asyncio
async def test_claim_marks_running_and_returns_none_when_empty(db_session, patched):
    assert await _claim_next_task() is None
    await graph_service._enqueue_build_task("u1", "note", "n1", "t", "h", {"text": "x"})
    task_id = await _claim_next_task()
    assert task_id is not None
    row = (await db_session.execute(select(GraphBuildTask))).scalar_one()
    assert row.status == "running"
    # 已无 pending → 认领不到
    assert await _claim_next_task() is None


@pytest.mark.asyncio
async def test_process_task_retry_then_failed(db_session, patched, monkeypatch):
    from app.core.background_init import init_manager
    monkeypatch.setattr(graph_service, "MAX_TASK_ATTEMPTS", 2)
    monkeypatch.setattr(init_manager, "chat_model",
                        make_fake_chat_model(['{"entities": [], "relations": []}']))

    async def _boom(*args, **kwargs):
        raise RuntimeError("LLM 不可用")
    monkeypatch.setattr(graph_service, "extract_entities", _boom)

    await graph_service._enqueue_build_task("u1", "doc", "md51", "f.pdf",
                                            content_hash("body"), {"text": "用 Python 写"})
    await db_session.execute(
        GraphDoc.__table__.insert().values(id="md51", user_id="u1", filename="f.pdf"))
    await db_session.commit()

    # 第一次消费：失败 → 重回 pending
    task_id = await _claim_next_task()
    assert await graph_service.process_task(task_id) is False
    row = (await db_session.execute(select(GraphBuildTask))).scalar_one()
    assert row.status == "pending" and row.attempts == 1

    # 第二次消费：达到上限 → failed 终态
    task_id = await _claim_next_task()
    assert await graph_service.process_task(task_id) is True
    await db_session.rollback()  # 结束本会话快照，读取其他会话的提交
    row = (await db_session.execute(select(GraphBuildTask))).scalar_one()
    assert row.status == "failed"
    assert "LLM 不可用" in row.error_message


@pytest.mark.asyncio
async def test_process_task_force_bypasses_hash_check(db_session, patched, monkeypatch):
    from app.core.background_init import init_manager
    monkeypatch.setattr(init_manager, "chat_model",
                        make_fake_chat_model(['{"entities": [{"name": "Python", "mentions": ["Python"]}], "relations": []}']))
    db_session.add(GraphExtractLog(id="logf", user_id="u1", note_id="n1",
                                   content_hash="h", status="success"))
    db_session.add(Note(id="n1", user_id="u1", title="标题", content="用 Python 写"))
    await db_session.commit()

    await graph_service._enqueue_build_task("u1", "note", "n1", "标题", "h", {}, force=True)
    task_id = await _claim_next_task()
    assert await graph_service.process_task(task_id) is True

    row = (await db_session.execute(select(GraphBuildTask))).scalar_one()
    assert row.status == "completed"
    await db_session.rollback()  # 结束本会话快照，读取其他会话的提交
    log = (await db_session.execute(
        select(GraphExtractLog).where(GraphExtractLog.note_id == "n1"))).scalar_one()
    assert log.status == "success"


@pytest.mark.asyncio
async def test_tick_drains_queue_in_order(db_session, patched, monkeypatch):
    from app.core.background_init import init_manager
    monkeypatch.setattr(init_manager, "chat_model",
                        make_fake_chat_model(['{"entities": [], "relations": []}']))
    db_session.add(Note(id="n1", user_id="u1", title="t1", content="a"))
    db_session.add(Note(id="n2", user_id="u1", title="t2", content="b"))
    await db_session.commit()
    await graph_service._enqueue_build_task("u1", "note", "n1", "t1", content_hash("a"), {"text": "a"})
    await graph_service._enqueue_build_task("u1", "note", "n2", "t2", content_hash("b"), {"text": "b"})

    assert await _tick() is True
    assert await _tick() is True
    assert await _tick() is False  # 队列空

    await db_session.rollback()  # 结束本会话快照，读取其他会话的提交
    rows = (await db_session.execute(select(GraphBuildTask))).scalars().all()
    assert all(r.status == "completed" for r in rows)
    logs = (await db_session.execute(select(GraphExtractLog))).scalars().all()
    assert {log.note_id for log in logs} == {"n1", "n2"}
