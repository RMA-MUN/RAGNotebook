
import os

import pytest
from sqlalchemy import select

from app.graph.services.graph_service import (
    _run_extraction,
    content_hash,
    maybe_schedule_doc_extraction,
    maybe_schedule_extraction,
)
from app.models.graph import (
    GraphBuildTask,
    GraphDoc,
    GraphExtractLog,
)
from app.models.note import Note
from tests.fakes import make_fake_chat_model

_neo4j_required = pytest.mark.skipif(
    not os.getenv("NEO4J_TEST_URI"), reason="需要真实 Neo4j（设 NEO4J_TEST_URI 启用）")


@pytest.fixture
def neo4j_env(monkeypatch):
    from app.core.failed_response import settings

    monkeypatch.setattr(settings, "NEO4J_URI", os.environ["NEO4J_TEST_URI"], raising=False)


def test_content_hash_stable():
    assert content_hash("hello") == content_hash("hello")
    assert content_hash("hello") != content_hash("world")


@pytest.mark.asyncio
@_neo4j_required
async def test_maybe_schedule_extraction_triggers_once(db_session, session_factory, monkeypatch,
                                                       neo4j_env, _cleanup):
    from app.core.background_init import init_manager
    from app.graph.services.graph_worker import _tick
    monkeypatch.setattr(init_manager, "chat_model",
                        make_fake_chat_model(['{"entities": [], "relations": []}']))
    monkeypatch.setattr("app.graph.services.graph_service.AsyncSessionLocal", session_factory)

    db_session.add(GraphExtractLog(id="log1", user_id="u1", note_id="n1",
                                   content_hash=content_hash("v1"), status="success"))
    db_session.add(Note(id="n1", user_id="u1", title="标题", content="v1"))
    await db_session.commit()

    # 入队幂等：同来源重复触发覆盖载荷
    assert await maybe_schedule_extraction("n1", "u1", "标题", "v1") is True
    assert await maybe_schedule_extraction("n1", "u1", "标题", "v1") is True

    # worker 消费：hash 与已有成功日志一致 → 跳过抽取，任务直接完成
    assert await _tick() is True
    await db_session.rollback()  # 结束本会话快照，读取其他会话的提交
    task = (await db_session.execute(
        select(GraphBuildTask).where(GraphBuildTask.source_id == "n1"))).scalar_one()
    assert task.status == "completed"
    log = (await db_session.execute(
        select(GraphExtractLog).where(GraphExtractLog.note_id == "n1"))).scalar_one()
    assert log.status == "success" and log.content_hash == content_hash("v1")

    # 内容变化 → 重新入队，worker 消费后重新抽取
    assert await maybe_schedule_extraction("n1", "u1", "标题", "v2") is True
    assert await _tick() is True
    await db_session.rollback()
    log = (await db_session.execute(
        select(GraphExtractLog).where(GraphExtractLog.note_id == "n1"))).scalar_one()
    assert log.content_hash == content_hash("v2")
    assert log.status == "success"


@pytest.mark.asyncio
async def test_maybe_schedule_doc_extraction_skips_same_hash(db_session, session_factory, monkeypatch):
    from app.graph.services.graph_worker import _tick
    monkeypatch.setattr("app.graph.services.graph_service.AsyncSessionLocal", session_factory)
    db_session.add(GraphExtractLog(id="logd", user_id="u1", note_id="md51",
                                   content_hash=content_hash("v1"), status="success", source_type="doc"))
    await db_session.commit()

    # 入队总是成功（哈希判重移到 worker 侧）
    assert await maybe_schedule_doc_extraction("u1", "md51", "报告.pdf", "v1") is True
    # 文档节点元数据在入队时就落库（否则总览图看不到文档节点）
    doc = (await db_session.execute(select(GraphDoc).where(GraphDoc.id == "md51"))).scalar_one_or_none()
    assert doc is not None and doc.filename == "报告.pdf"

    # worker 消费：hash 一致 → 跳过抽取，任务完成且日志保持原样
    assert await _tick() is True
    task = (await db_session.execute(
        select(GraphBuildTask).where(GraphBuildTask.source_id == "md51"))).scalar_one()
    assert task.status == "completed"
    log = (await db_session.execute(select(GraphExtractLog).where(
        GraphExtractLog.note_id == "md51"))).scalar_one()
    assert log.status == "success" and log.content_hash == content_hash("v1")


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
async def test_run_extraction_doc_aborts_when_source_deleted_mid_flight(db_session, session_factory, monkeypatch):
    """文档在抽取（LLM 调用）期间被删除 → 抽取必须放弃，不得复活实体/关联/日志。"""
    from app.core.background_init import init_manager
    monkeypatch.setattr(init_manager, "chat_model",
                        make_fake_chat_model(['{"entities": [{"name": "Python", "mentions": ["Python"]}], "relations": []}']))
    monkeypatch.setattr("app.graph.services.graph_service.AsyncSessionLocal", session_factory)
    # 只残留 pending 日志（GraphDoc 已被删除——模拟"清理先行、抽取后至"的竞态）
    db_session.add(GraphExtractLog(id="logx", user_id="u1", note_id="md51",
                                   content_hash="h", status="pending", source_type="doc"))
    await db_session.commit()

    await _run_extraction("md51", "u1", "报告.pdf", content_hash("body"),
                          body="用 Python 写", source_type="doc")

    log = (await db_session.execute(select(GraphExtractLog).where(
        GraphExtractLog.note_id == "md51"))).scalars().all()
    assert log == []
