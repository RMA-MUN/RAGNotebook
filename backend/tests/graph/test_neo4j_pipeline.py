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


@pytest.mark.asyncio
async def test_note_pipeline_wiki_edges_first_match(db_session, session_factory, monkeypatch,
                                                    neo4j_env, _cleanup):
    """[[双链]] 命中已存在笔记才建 WIKI 边；重名笔记首条匹配生效，不得 MultipleResultsFound。"""
    from sqlalchemy import select

    from app.graph.storage import neo4j_client
    from app.models.note import Note

    uid = "pipe-" + uuid.uuid4().hex[:8]
    db_session.add(Note(id="n1", user_id=uid, title="我的笔记", content="[[FastAPI]]"))
    db_session.add(Note(id="n2", user_id=uid, title="FastAPI", content="a"))
    db_session.add(Note(id="n3", user_id=uid, title="FastAPI", content="b"))
    await db_session.commit()
    monkeypatch.setattr("app.graph.services.graph_service.AsyncSessionLocal", session_factory)
    monkeypatch.setattr(
        background_init.init_manager, "chat_model",
        make_fake_chat_model(['{"entities": [], "relations": []}']))

    await _run_extraction("n1", uid, "我的笔记", content_hash("[[FastAPI]]"), body="[[FastAPI]]")

    driver = neo4j_client.get_neo4j_driver()
    edges = await driver.execute_query(
        "MATCH (:Note {id: 'n1', user_id: $uid})-[w:WIKI]->(t:Note) "
        "RETURN t.id AS tid", {"uid": uid})
    assert [r["tid"] for r in edges.records] == ["n2"]


@pytest.mark.asyncio
async def test_doc_pipeline_skips_note_edges(db_session, session_factory, monkeypatch,
                                             neo4j_env, _cleanup):
    """文档正文里即便有 [[双链]] 语法也不生成笔记 WIKI 边；实体照常落图。"""
    from sqlalchemy import select

    from app.graph.storage import neo4j_client
    from app.models.graph import GraphDoc

    uid = "pipe-" + uuid.uuid4().hex[:8]
    db_session.add(GraphDoc(id="md5-doc", user_id=uid, filename="报告.pdf"))
    await db_session.commit()
    monkeypatch.setattr("app.graph.services.graph_service.AsyncSessionLocal", session_factory)
    monkeypatch.setattr(
        background_init.init_manager, "chat_model",
        make_fake_chat_model(
            ['{"entities": [{"name": "Python", "type": "tech", "mentions": ["Python"]}], "relations": []}']))

    await _run_extraction("md5-doc", uid, "报告.pdf", content_hash("body"),
                          body="用 [[FastAPI]] 与 Python", source_type="doc")

    driver = neo4j_client.get_neo4j_driver()
    wiki = await driver.execute_query(
        "MATCH (:Note {user_id: $uid})-[w:WIKI]->() RETURN count(w) AS c", {"uid": uid})
    assert wiki.records[0]["c"] == 0
    entity_cnt = await driver.execute_query(
        "MATCH (e:Entity {user_id: $uid}) RETURN count(e) AS c", {"uid": uid})
    assert entity_cnt.records[0]["c"] == 1


@pytest.mark.asyncio
async def test_doc_pipeline_creates_missing_log_row(db_session, session_factory, monkeypatch,
                                                    neo4j_env, _cleanup):
    """日志行缺失（抽取期间被清理/从未持久化）时不得崩溃，应自建日志行并落 success。"""
    from sqlalchemy import select

    from app.models.graph import GraphDoc, GraphExtractLog

    uid = "pipe-" + uuid.uuid4().hex[:8]
    db_session.add(GraphDoc(id="md5-nolog", user_id=uid, filename="报告.pdf"))
    await db_session.commit()
    monkeypatch.setattr("app.graph.services.graph_service.AsyncSessionLocal", session_factory)
    monkeypatch.setattr(
        background_init.init_manager, "chat_model",
        make_fake_chat_model(
            ['{"entities": [{"name": "Python", "type": "tech", "mentions": ["Python"]}], "relations": []}']))

    await _run_extraction("md5-nolog", uid, "报告.pdf", content_hash("body"),
                          body="用 Python 写", source_type="doc")

    log = (await db_session.execute(select(GraphExtractLog).where(
        GraphExtractLog.note_id == "md5-nolog"))).scalar_one()
    assert log.status == "success" and log.content_hash == content_hash("body")


@pytest.mark.asyncio
async def test_cleanup_note_graph_removes_graph_and_logs(db_session, session_factory, monkeypatch,
                                                         neo4j_env, _cleanup):
    """笔记删除联动：Neo4j 图节点/边清理 + MySQL 抽取日志删除。"""
    from sqlalchemy import select

    from app.graph.services.graph_service import cleanup_note_graph
    from app.models.graph import GraphExtractLog
    from app.models.note import Note

    uid = "pipe-" + uuid.uuid4().hex[:8]
    db_session.add(Note(id="n-del", user_id=uid, title="待删笔记", content="本体"))
    await db_session.commit()
    monkeypatch.setattr("app.graph.services.graph_service.AsyncSessionLocal", session_factory)
    monkeypatch.setattr(
        background_init.init_manager, "chat_model",
        make_fake_chat_model(
            ['{"entities": [{"name": "临时实体", "type": "concept", "mentions": ["临时实体"]}], "relations": []}']))

    await _run_extraction("n-del", uid, "待删笔记", content_hash("body"), body="临时实体")

    from app.graph.storage import neo4j_client

    driver = neo4j_client.get_neo4j_driver()
    entity_cnt = await driver.execute_query(
        "MATCH (e:Entity {user_id: $uid}) RETURN count(e) AS c", {"uid": uid})
    assert entity_cnt.records[0]["c"] == 1

    await cleanup_note_graph(db_session, uid, "n-del")
    assert (await db_session.execute(select(GraphExtractLog).where(
        GraphExtractLog.user_id == uid))).scalars().all() == []
    entity_cnt = await driver.execute_query(
        "MATCH (e:Entity {user_id: $uid}) RETURN count(e) AS c", {"uid": uid})
    assert entity_cnt.records[0]["c"] == 0


@pytest.mark.asyncio
async def test_extraction_replaces_stale_relations_keeps_manual(db_session, session_factory,
                                                                monkeypatch, neo4j_env, _cleanup):
    """重抽按来源溯源替换旧关系；无溯源标记的手动关系（图谱页手工连线）不受影响。"""
    from sqlalchemy import select

    from app.graph.schemas.graph import EntityIn, RelationIn
    from app.graph.storage import neo4j_client
    from app.graph.storage.neo4j_graph_store import Neo4jGraphStore
    from app.models.graph import GraphDoc

    uid = "pipe-" + uuid.uuid4().hex[:8]
    db_session.add(GraphDoc(id="md5-rel", user_id=uid, filename="报告.pdf"))
    await db_session.commit()
    monkeypatch.setattr("app.graph.services.graph_service.AsyncSessionLocal", session_factory)
    monkeypatch.setattr(
        background_init.init_manager, "chat_model",
        make_fake_chat_model([
            '{"entities": [{"name": "实体A", "type": "concept", "mentions": ["A"]}, '
            '{"name": "实体B", "type": "concept", "mentions": ["B"]}], '
            '"relations": [{"source": "实体A", "target": "实体B", "relation_type": "新关系"}]}']))

    # 预置：两个实体 + 手动关系（无溯源）+ 旧抽取关系（doc 溯源）
    store = Neo4jGraphStore()
    ea = await store.upsert_entity(uid, EntityIn(name="实体A"))
    eb = await store.upsert_entity(uid, EntityIn(name="实体B"))
    await store.create_relation(uid, RelationIn(
        source_id=ea.id, target_id=eb.id, relation_type="手动关系"))
    await store.set_relations_from_source(uid, "doc", "md5-rel", [
        {"source_id": ea.id, "target_id": eb.id, "relation_type": "旧关系"}])

    await _run_extraction("md5-rel", uid, "报告.pdf", content_hash("body"),
                          body="A 与 B", source_type="doc")

    driver = neo4j_client.get_neo4j_driver()
    rels = await driver.execute_query(
        "MATCH (a:Entity {user_id: $uid})-[r:RELATES_TO]->(b:Entity {user_id: $uid}) "
        "RETURN r.relation_type AS t, r.source_type AS stype", {"uid": uid})
    by_type = {r["t"]: r["stype"] for r in rels.records}
    assert set(by_type) == {"手动关系", "新关系"}
    # 手动关系无溯源标记（source_type 为空）；抽取关系带 doc 溯源
    assert by_type["手动关系"] is None
    assert by_type["新关系"] == "doc"
