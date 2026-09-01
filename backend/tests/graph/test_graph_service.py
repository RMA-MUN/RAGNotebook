
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


class _RecorderStore:
    """截获 upsert_chunks 的最小 GraphStore 替身，避免 Nea4j 依赖。"""

    def __init__(self):
        self.upserted = None

    async def list_types(self, user_id):
        return []

    async def set_relations_from_source(self, user_id, source_type, source_id, rels):
        return None

    async def source_entity_candidates(self, user_id, source_type, source_id):
        return []

    async def ensure_source_node(self, user_id, source_type, source_id, title):
        return None

    async def set_source_mentions(self, user_id, source_type, source_id, links):
        return None

    async def upsert_chunks(self, user_id, source_type, source_id, title, chunk_payloads):
        self.upserted = chunk_payloads

    async def set_chunk_mentions(self, user_id, source_type, source_id, chunk_indexes):
        return None

    async def sweep_orphan_entities(self, user_id, entity_ids, keep_sources):
        return None


class _EmbedRecorder:
    def __init__(self, vectors=None):
        self.calls = 0
        self.last_texts = None
        self._vectors = vectors or [[0.1, 0.2]]

    def embed_documents(self, texts):
        self.calls += 1
        self.last_texts = texts
        return [self._vectors[0] for _ in texts]


@pytest.mark.asyncio
async def test_run_extraction_uses_per_user_embed_model(db_session, session_factory, monkeypatch):
    """per-user embed 解析成功时覆盖全局模型（文档带预切 chunks 的写入管线）。"""
    from app.core.background_init import init_manager
    from app.graph.schemas.graph import ExtractResult

    monkeypatch.setattr("app.graph.services.graph_service.AsyncSessionLocal", session_factory)
    db_session.add(GraphDoc(id="md51", user_id="u1", filename="报告.pdf"))
    await db_session.commit()

    store = _RecorderStore()
    monkeypatch.setattr("app.graph.services.graph_service.get_graph_store", lambda db: store)

    per_user = _EmbedRecorder()

    class _GlobalPoison:
        def embed_documents(self, texts):
            raise AssertionError("per-user embed 解析成功时不应使用全局 embed 模型")

    monkeypatch.setattr(init_manager, "embed_model", _GlobalPoison())
    monkeypatch.setattr(init_manager, "chat_model", object())

    async def _resolve_embed(user_id):
        return per_user

    async def _resolve_chat(user_id):
        return object()

    async def _fake_extract(title, body, chat_model):
        return ExtractResult(entities=[], relations=[])

    monkeypatch.setattr("app.graph.services.graph_service.create_embed_model_for_user", _resolve_embed)
    monkeypatch.setattr("app.graph.services.graph_service.create_chat_model_for_user", _resolve_chat)
    monkeypatch.setattr("app.graph.services.graph_service.extract_entities", _fake_extract)

    await _run_extraction("md51", "u1", "报告.pdf", content_hash("body"),
                          body="用 Python 写", source_type="doc",
                          chunks=[{"chunk_index": 0, "text": "Python"}])

    assert per_user.calls == 1
    assert per_user.last_texts == ["Python"]
    assert store.upserted[0]["embedding"] == [0.1, 0.2]


@pytest.mark.asyncio
async def test_run_extraction_falls_back_to_global_embed_on_per_user_failure(
        db_session, session_factory, monkeypatch):
    """per-user embed 解析抛错时回落全局 embed 模型，不阻断写入。"""
    from app.core.background_init import init_manager
    from app.graph.schemas.graph import ExtractResult

    monkeypatch.setattr("app.graph.services.graph_service.AsyncSessionLocal", session_factory)
    db_session.add(GraphDoc(id="md52", user_id="u1", filename="报告.pdf"))
    await db_session.commit()

    store = _RecorderStore()
    monkeypatch.setattr("app.graph.services.graph_service.get_graph_store", lambda db: store)

    global_embed = _EmbedRecorder()
    monkeypatch.setattr(init_manager, "embed_model", global_embed)
    monkeypatch.setattr(init_manager, "chat_model", object())

    async def _resolve_embed(user_id):
        raise ValueError("per-user 配置不完整")

    async def _resolve_chat(user_id):
        return object()

    async def _fake_extract(title, body, chat_model):
        return ExtractResult(entities=[], relations=[])

    monkeypatch.setattr("app.graph.services.graph_service.create_embed_model_for_user", _resolve_embed)
    monkeypatch.setattr("app.graph.services.graph_service.create_chat_model_for_user", _resolve_chat)
    monkeypatch.setattr("app.graph.services.graph_service.extract_entities", _fake_extract)

    await _run_extraction("md52", "u1", "报告.pdf", content_hash("body"),
                          body="用 Python 写", source_type="doc",
                          chunks=[{"chunk_index": 0, "text": "Python"}])

    assert global_embed.calls == 1
    assert store.upserted[0]["embedding"] == [0.1, 0.2]
