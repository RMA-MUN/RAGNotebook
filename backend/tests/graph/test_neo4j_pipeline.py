"""抽取管线 Neo4j 主路径端到端测试（需要真实 Neo4j）。

    NEO4J_TEST_URI=bolt://localhost:7687 uv run --extra dev python -m pytest tests/graph/test_neo4j_pipeline.py -v

复用 test_note_extraction_hook 的手法：SQLite 承载 Note/GraphDoc/extract_logs，
monkeypatch 会话工厂与假 chat/embed 模型，settings.NEO4J_URI 指向真库 →
工厂返回 Neo4jGraphStore，验证管线完整写入图数据库。
"""
import os
import uuid

import pytest

from app.core import background_init
from app.graph.services.graph_service import _run_extraction, content_hash
from tests.fakes import make_fake_chat_model

pytestmark = pytest.mark.skipif(
    not os.getenv("NEO4J_TEST_URI"), reason="需要真实 Neo4j（设 NEO4J_TEST_URI 启用）")

DIM = 8  # 与 schema 探针对齐


class FakeEmbed:
    """定维假嵌入模型：按文本哈希选正交基方向，同文本同向量。"""

    def embed_documents(self, texts):
        return [self.embed_query(t) for t in texts]

    def embed_query(self, text):
        v = [0.0] * DIM
        v[hash(text) % DIM] = 1.0
        return v


@pytest.fixture
def neo4j_env(monkeypatch):
    from app.core.failed_response import settings

    monkeypatch.setattr(settings, "NEO4J_URI", os.environ["NEO4J_TEST_URI"], raising=False)
    monkeypatch.setattr(background_init.init_manager, "embed_model", FakeEmbed())


@pytest.fixture
async def _cleanup():
    yield
    from app.graph.storage import neo4j_client

    driver = neo4j_client.get_neo4j_driver()
    # 按前缀清理本测试文件产生的用户子图
    await driver.execute_query(
        "MATCH (n) WHERE n.user_id STARTS WITH 'pipe-' DETACH DELETE n")


@pytest.mark.asyncio
async def test_note_pipeline_writes_neo4j(db_session, session_factory, monkeypatch, neo4j_env, _cleanup):
    from sqlalchemy import select

    from app.graph.storage import neo4j_client
    from app.graph.storage.neo4j_graph_store import Neo4jGraphStore
    from app.models.graph import GraphExtractLog
    from app.models.note import Note

    uid = "pipe-" + uuid.uuid4().hex[:8]
    db_session.add(Note(id="n1", user_id=uid, title="图谱笔记", content="本体"))
    await db_session.commit()
    monkeypatch.setattr("app.graph.services.graph_service.AsyncSessionLocal", session_factory)
    monkeypatch.setattr(
        background_init.init_manager, "chat_model",
        make_fake_chat_model([
            '{"entities": [{"name": "知识图谱", "type": "concept", "mentions": ["知识图谱"]}, '
            '{"name": "Neo4j", "type": "tech", "mentions": ["Neo4j"]}], '
            '"relations": [{"source": "知识图谱", "target": "Neo4j", "relation_type": "存储于"}]}'
        ]))

    body = "知识图谱存储选型讨论，正文较长。" * 30 + " Neo4j 是图数据库。"
    await _run_extraction("n1", uid, "图谱笔记", content_hash(body), body=body, source_type="note")

    # MySQL 侧：抽取日志成功
    log = (await db_session.execute(
        select(GraphExtractLog).where(GraphExtractLog.note_id == "n1"))).scalar_one()
    assert log.status == "success"

    # Neo4j 侧：来源节点/实体/关系/Chunk/两级 MENTIONS 全部落图
    driver = neo4j_client.get_neo4j_driver()
    note_cnt = await driver.execute_query(
        "MATCH (n:Note {user_id: $uid}) RETURN count(n) AS c", {"uid": uid})
    assert note_cnt.records[0]["c"] == 1
    entity_cnt = await driver.execute_query(
        "MATCH (e:Entity {user_id: $uid}) RETURN count(e) AS c", {"uid": uid})
    assert entity_cnt.records[0]["c"] >= 2
    chunk_cnt = await driver.execute_query(
        "MATCH (c:Chunk {user_id: $uid}) RETURN count(c) AS c", {"uid": uid})
    assert chunk_cnt.records[0]["c"] >= 1

    rel = await driver.execute_query(
        "MATCH (a:Entity)-[r:RELATES_TO]->(b:Entity) WHERE a.user_id=$uid "
        "RETURN r.relation_type AS t LIMIT 1", {"uid": uid})
    assert rel.records[0]["t"] == "存储于"

    chunk_mention = await driver.execute_query(
        "MATCH (:Chunk)-[:MENTIONS]->(e:Entity) WHERE e.user_id=$uid RETURN count(*) AS c",
        {"uid": uid})
    assert chunk_mention.records[0]["c"] >= 1

    src_mention = await driver.execute_query(
        "MATCH (:Note)-[:MENTIONS]->(e:Entity) WHERE e.user_id=$uid RETURN count(*) AS c",
        {"uid": uid})
    assert src_mention.records[0]["c"] >= 1

    # store 侧 API 也能读回（画布/检索共用）
    store = Neo4jGraphStore()
    view = await store.get_note_graph(uid, "n1")
    assert any(n.node_type == "entity" for n in view.nodes)


@pytest.mark.asyncio
async def test_doc_pipeline_with_prechunks(db_session, session_factory, monkeypatch, neo4j_env, _cleanup):
    from sqlalchemy import select

    from app.graph.storage import neo4j_client
    from app.models.graph import GraphDoc, GraphExtractLog

    uid = "pipe-" + uuid.uuid4().hex[:8]
    db_session.add(GraphDoc(id="md5-x", user_id=uid, filename="doc.pdf"))
    await db_session.commit()
    monkeypatch.setattr("app.graph.services.graph_service.AsyncSessionLocal", session_factory)
    monkeypatch.setattr(
        background_init.init_manager, "chat_model",
        make_fake_chat_model([
            '{"entities": [{"name": "报表", "type": "concept", "mentions": ["报表"]}], "relations": []}'
        ]))

    body = "这是一份季度报表，包含营收数据。" * 10
    pre_chunks = [{"chunk_index": 0, "text": body[:50], "page": 1, "image_paths": ["img1.png"]},
                  {"chunk_index": 1, "text": body[50:], "page": 2, "image_paths": []}]
    await _run_extraction("md5-x", uid, "doc.pdf", content_hash(body), body=body,
                          source_type="doc", chunks=pre_chunks)

    log = (await db_session.execute(
        select(GraphExtractLog).where(GraphExtractLog.note_id == "md5-x"))).scalar_one()
    assert log.status == "success"

    driver = neo4j_client.get_neo4j_driver()
    chunk_rows = await driver.execute_query(
        "MATCH (c:Chunk {user_id: $uid}) RETURN c.page AS page, c.image_paths AS imgs, c.text AS text "
        "ORDER BY c.chunk_index", {"uid": uid})
    records = list(chunk_rows.records)
    assert len(records) == 2
    assert records[0]["page"] == 1 and records[0]["imgs"] == ["img1.png"]

    src_mention = await driver.execute_query(
        "MATCH (:Doc)-[:MENTIONS]->(e:Entity) WHERE e.user_id=$uid RETURN count(*) AS c", {"uid": uid})
    assert src_mention.records[0]["c"] >= 1
