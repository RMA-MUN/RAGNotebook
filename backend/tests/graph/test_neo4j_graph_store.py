"""Neo4jGraphStore 集成测试（需要真实 Neo4j）。

启用方式：
    NEO4J_TEST_URI=bolt://localhost:7687 uv run --extra dev python -m pytest tests/graph/test_neo4j_graph_store.py -v

每个测试用独立随机 user_id 隔离，结束后清除该用户全部节点；系统类型种子是全局的、不清理。
"""
import os
import uuid

import pytest

pytestmark = pytest.mark.skipif(
    not os.getenv("NEO4J_TEST_URI"), reason="需要真实 Neo4j（设 NEO4J_TEST_URI 启用）")

DIM = 8  # 与 test_neo4j_client 的 schema 探针对齐，向量索引固定 8 维


@pytest.fixture(scope="module")
async def _schema():
    from app.graph.storage import neo4j_client

    mp = pytest.MonkeyPatch()
    mp.setattr(neo4j_client.settings, "NEO4J_URI", os.environ["NEO4J_TEST_URI"], raising=False)

    class _Probe:
        def embed_query(self, text):
            return [0.0] * DIM

    await neo4j_client.ensure_graph_schema(_Probe())
    yield
    mp.undo()


@pytest.fixture
def store(_schema, monkeypatch):
    from app.core.failed_response import settings
    from app.graph.storage import get_graph_store

    monkeypatch.setattr(settings, "NEO4J_URI", os.environ["NEO4J_TEST_URI"], raising=False)
    return get_graph_store()


@pytest.fixture
def uid():
    return "t-" + uuid.uuid4().hex[:8]


@pytest.fixture
async def _cleanup(uid):
    yield
    from app.graph.storage import neo4j_client

    driver = neo4j_client.get_neo4j_driver()
    await driver.execute_query("MATCH (n) WHERE n.user_id = $uid DETACH DELETE n", {"uid": uid})


def _vec(basis: int) -> list[float]:
    """第 basis 维为 1 的正交基向量（cosine 只看方向，标量倍数向量彼此同向）。"""
    v = [0.0] * DIM
    v[basis % DIM] = 1.0
    return v


@pytest.mark.asyncio
async def test_entity_upsert_create_update_alias(store, uid, _cleanup):
    from app.graph.schemas.graph import EntityIn

    e = await store.upsert_entity(uid, EntityIn(name="Python", display_name="Python", confidence=0.8,
                                                aliases=["py"], source_note_ids=["n1"]))
    e2 = await store.upsert_entity(uid, EntityIn(name="Python", confidence=0.9, source_note_ids=["n2"]))
    assert e2.id == e.id
    assert e2.confidence == 0.9 and e2.source_note_ids == ["n1", "n2"]
    # 别名命中：以别名 "py" 写入应并入同一实体
    e3 = await store.upsert_entity(uid, EntityIn(name="py", confidence=0.5))
    assert e3.id == e.id
    assert "py" in e3.aliases


@pytest.mark.asyncio
async def test_search_entities_contains(store, uid, _cleanup):
    from app.graph.schemas.graph import EntityIn

    await store.upsert_entity(uid, EntityIn(name="FastAPI", display_name="FastAPI"))
    await store.upsert_entity(uid, EntityIn(name="Flask"))
    hits = await store.search_entities(uid, "fast", limit=10)
    assert [x.name for x in hits] == ["FastAPI"]


@pytest.mark.asyncio
async def test_neighbors_and_merge_redirect(store, uid, _cleanup):
    from app.graph.schemas.graph import EntityIn, RelationIn

    e1 = await store.upsert_entity(uid, EntityIn(name="Python"))
    e2 = await store.upsert_entity(uid, EntityIn(name="FastAPI"))
    e3 = await store.upsert_entity(uid, EntityIn(name="Neo4j"))
    await store.create_relation(uid, RelationIn(source_id=e1.id, target_id=e2.id, relation_type="基于"))
    await store.create_relation(uid, RelationIn(source_id=e2.id, target_id=e3.id, relation_type="存储于"))

    view = await store.get_neighbors(uid, e1.id, depth=2)
    assert {n.id for n in view.nodes} == {e1.id, e2.id, e3.id}
    assert len(view.edges) == 2

    # merge e2 → e1：两条关系都重定向到 e1
    merged = await store.merge_entities(uid, e1.id, e2.id)
    assert merged.id == e1.id
    view2 = await store.get_neighbors(uid, e1.id, depth=1)
    assert {n.id for n in view2.nodes} == {e1.id, e3.id}
    assert all(e.source != e2.id and e.target != e2.id for e in view2.edges)


@pytest.mark.asyncio
async def test_types_crud(store, uid, _cleanup):
    from app.graph.schemas.graph import TypeIn

    system_types = await store.list_types(uid)
    assert any(t.name == "person" for t in system_types)
    t = await store.upsert_type(uid, TypeIn(name="org", display_name="组织", color="#00FF00"))
    types = await store.list_types(uid)
    assert any(x.id == t.id for x in types)
    # 删除类型后引用它的实体 type_id 置空
    from app.graph.schemas.graph import EntityIn

    e = await store.upsert_entity(uid, EntityIn(name="Docker", type_id=t.id))
    await store.delete_type(uid, t.id)
    assert (await store.get_entity(uid, e.id)).type_id is None


@pytest.mark.asyncio
async def test_chunk_lifecycle_and_retrieval(store, uid, _cleanup):
    from app.graph.schemas.graph import EntityIn

    await store.ensure_source_node(uid, "note", "note-1", "我的笔记")
    chunks = [
        {"chunk_index": 0, "text": "知识图谱是一种结构化知识表示技术", "embedding": _vec(0)},
        {"chunk_index": 1, "text": "Python 是常用的编程语言", "embedding": _vec(1)},
    ]
    await store.upsert_chunks(uid, "note", "note-1", "我的笔记", chunks)

    e1 = await store.upsert_entity(uid, EntityIn(name="知识图谱"))
    e2 = await store.upsert_entity(uid, EntityIn(name="Python"))
    await store.set_source_mentions(uid, "note", "note-1", [
        {"entity_id": e1.id, "mention_count": 2, "context": [{"snippet": "知识图谱是一种"}]},
        {"entity_id": e2.id, "mention_count": 1, "context": []},
    ])
    # Chunk 级 MENTIONS：规则匹配结果落边（知识图谱→chunk0，Python→chunk1）
    await store.set_chunk_mentions(uid, "note", "note-1", [
        {"entity_id": e1.id, "chunk_indexes": [0]},
        {"entity_id": e2.id, "chunk_indexes": [1]},
    ])
    await store.set_relations_from_source(uid, "note", "note-1", [
        {"source_id": e1.id, "target_id": e2.id, "relation_type": "实现语言"}])

    # 来源级关联 → EntityNoteLink
    links = await store.get_entity_notes(uid, e1.id)
    assert len(links) == 1 and links[0].note_id == "note-1"
    assert links[0].source_type == "note" and links[0].mention_count == 2

    # Chunk 级 MENTIONS：图扩展证据
    hits = await store.get_chunks_mentioning(uid, [e1.id, e2.id], limit=5)
    assert {h.chunk_index for h in hits} == {0, 1}

    # 向量检索：与 chunk0 同向的查询向量排前
    vec_hits = await store.search_chunks(uid, _vec(0), None, None, limit=2)
    assert vec_hits[0].chunk_index == 0
    # 全文检索（cjk 分词）
    ft_hits = await store.search_chunks(uid, None, "知识图谱", None, limit=2)
    assert any(h.chunk_index == 0 for h in ft_hits)
    # kind 过滤
    doc_hits = await store.search_chunks(uid, None, "知识图谱", ["doc"], limit=2)
    assert doc_hits == []

    # 笔记图：note 节点 + 提及实体 + 实体关系
    view = await store.get_note_graph(uid, "note-1")
    node_types = {n.id: n.node_type for n in view.nodes}
    assert node_types["note-1"] == "note" and node_types[e1.id] == "entity"
    assert any(x.relation_type == "提及" for x in view.edges)
    assert any(x.relation_type == "实现语言" for x in view.edges)

    # overview：实体 + 笔记节点 + 提及边
    overview = await store.get_overview(uid, None, 20)
    assert {n.id for n in overview.nodes} >= {"note-1", e1.id, e2.id}
    overview_filtered = await store.get_overview(uid, ["nonexistent-type"], 20)
    assert all(n.node_type != "entity" for n in overview_filtered.nodes)


@pytest.mark.asyncio
async def test_wiki_edges_and_doc_graph(store, uid, _cleanup):
    from app.graph.schemas.graph import EntityIn

    await store.ensure_source_node(uid, "note", "note-a", "笔记A")
    await store.ensure_source_node(uid, "note", "note-b", "笔记B")
    await store.set_note_wiki_edges(uid, "note-a", [{"target_note_id": "note-b", "kind": "wiki"}])
    # 单向存储，无向查询两侧都能看到
    va = await store.get_note_graph(uid, "note-a")
    vb = await store.get_note_graph(uid, "note-b")
    assert any(e.kind == "wiki" for e in va.edges)
    assert any(e.kind == "wiki" for e in vb.edges)
    # 重设为空 → 出边清空
    await store.set_note_wiki_edges(uid, "note-a", [])
    va2 = await store.get_note_graph(uid, "note-a")
    assert not any(e.kind == "wiki" for e in va2.edges)

    # 目标 Note 节点尚未建出（先链接后创建/回填乱序）→ 按链接标题就地补建目标节点并建边
    await store.ensure_source_node(uid, "note", "note-a", "笔记A")
    await store.set_note_wiki_edges(uid, "note-a",
                                    [{"target_note_id": "note-late", "target_title": "笔记C", "kind": "wiki"}])
    vl = await store.get_note_graph(uid, "note-a")
    assert any(e.kind == "wiki" and e.target == "note-late" for e in vl.edges)
    late_node = next(n for n in vl.nodes if n.id == "note-late")
    assert late_node.label == "笔记C"

    # 文档子图
    await store.ensure_source_node(uid, "doc", "md5-1", "report.pdf")
    e = await store.upsert_entity(uid, EntityIn(name="报表"))
    await store.set_source_mentions(uid, "doc", "md5-1",
                                    [{"entity_id": e.id, "mention_count": 1, "context": []}])
    links = await store.get_entity_notes(uid, e.id)
    assert links[0].source_type == "doc" and links[0].source_name == "report.pdf"
    view = await store.get_doc_graph(uid, "md5-1")
    assert any(n.id == "md5-1" and n.node_type == "doc" and n.label == "report.pdf" for n in view.nodes)


@pytest.mark.asyncio
async def test_clear_source_and_orphan_sweep(store, uid, _cleanup):
    from app.graph.schemas.graph import EntityIn

    e_shared = await store.upsert_entity(uid, EntityIn(name="共享实体"))
    e_orphan = await store.upsert_entity(uid, EntityIn(name="孤儿实体"))

    await store.ensure_source_node(uid, "note", "note-1", "笔记1")
    await store.set_source_mentions(uid, "note", "note-1", [
        {"entity_id": e_shared.id, "mention_count": 1, "context": []},
        {"entity_id": e_orphan.id, "mention_count": 1, "context": []},
    ])
    # 共享实体同时被另一来源提及
    await store.ensure_source_node(uid, "note", "note-2", "笔记2")
    await store.set_source_mentions(uid, "note", "note-2",
                                    [{"entity_id": e_shared.id, "mention_count": 1, "context": []}])

    candidates = await store.clear_source_data(uid, "note", "note-1")
    assert set(candidates) == {e_shared.id, e_orphan.id}
    await store.sweep_orphan_entities(uid, candidates, ["note-1"])

    assert await store.get_entity(uid, e_shared.id) is not None   # 仍有 note-2 关联
    assert await store.get_entity(uid, e_orphan.id) is None       # 孤儿被级联删除


@pytest.mark.asyncio
async def test_clear_all_docs(store, uid, _cleanup):
    from app.graph.schemas.graph import EntityIn

    await store.ensure_source_node(uid, "doc", "md5-1", "a.pdf")
    await store.upsert_chunks(uid, "doc", "md5-1", "a.pdf",
                              [{"chunk_index": 0, "text": "文档内容", "embedding": _vec(3)}])
    e = await store.upsert_entity(uid, EntityIn(name="文档实体"))
    await store.set_source_mentions(uid, "doc", "md5-1",
                                    [{"entity_id": e.id, "mention_count": 1, "context": []}])

    await store.clear_all_docs(uid)
    assert await store.get_entity(uid, e.id) is None  # 失去唯一来源 → 孤儿清扫
    doc_hits = await store.search_chunks(uid, _vec(3), None, None, limit=5)
    assert doc_hits == []


@pytest.mark.asyncio
async def test_update_entity_rename_and_conflict(store, uid, _cleanup):
    from app.graph.schemas.graph import EntityIn

    e = await store.upsert_entity(uid, EntityIn(name="旧名", confidence=0.5))
    updated = await store.update_entity(uid, e.id, EntityIn(name="新名", confidence=0.9))
    assert updated.id == e.id and updated.name == "新名"
    assert await store.search_entities(uid, "旧名", 5) == []  # 改名不新建、旧名不残留

    await store.upsert_entity(uid, EntityIn(name="另一实体", confidence=0.5))
    with pytest.raises(ValueError):
        await store.update_entity(uid, e.id, EntityIn(name="另一实体", confidence=0.5))


@pytest.mark.asyncio
async def test_create_relation_missing_entity_raises(store, uid, _cleanup):
    from app.graph.schemas.graph import RelationIn

    with pytest.raises(ValueError):
        await store.create_relation(uid, RelationIn(
            source_id="ghost-a", target_id="ghost-b", relation_type="x"))


@pytest.mark.asyncio
async def test_multi_user_same_md5_isolated(store, _cleanup):
    """P0 回归：两个用户上传同一 md5 文档，Doc/Chunk/提及按用户隔离互不可见。"""
    from app.graph.schemas.graph import EntityIn
    from app.graph.storage import neo4j_client

    uid_a, uid_b = "ta" + uuid.uuid4().hex[:8], "tb" + uuid.uuid4().hex[:8]
    md5 = "same" + uuid.uuid4().hex[:8]
    driver = neo4j_client.get_neo4j_driver()
    try:
        await store.ensure_source_node(uid_a, "doc", md5, "同一文件.pdf")
        await store.ensure_source_node(uid_b, "doc", md5, "同一文件.pdf")
        ea = (await store.upsert_entity(uid_a, EntityIn(
            name="共享实体", confidence=0.8, source_note_ids=[md5])))
        eb = (await store.upsert_entity(uid_b, EntityIn(
            name="共享实体", confidence=0.8, source_note_ids=[md5])))
        assert ea.id != eb.id  # 实体按 (user_id, name) 隔离
        await store.set_source_mentions(uid_a, "doc", md5, [
            {"entity_id": ea.id, "mention_count": 1, "context": []}])
        await store.set_source_mentions(uid_b, "doc", md5, [
            {"entity_id": eb.id, "mention_count": 1, "context": []}])
        await store.upsert_chunks(uid_a, "doc", md5, "同一文件.pdf",
                                  [{"chunk_index": 0, "text": "用户A的正文"}])
        await store.upsert_chunks(uid_b, "doc", md5, "同一文件.pdf",
                                  [{"chunk_index": 0, "text": "用户B的正文"}])
        await store.set_chunk_mentions(uid_a, "doc", md5,
                                       [{"entity_id": ea.id, "chunk_indexes": [0]}])
        await store.set_chunk_mentions(uid_b, "doc", md5,
                                       [{"entity_id": eb.id, "chunk_indexes": [0]}])

        # 各自的 chunk 提及查询只见自己的正文（修复前同 id Chunk 互相覆盖/串数据）
        hits_a = await store.get_chunks_mentioning(uid_a, [ea.id], 5)
        hits_b = await store.get_chunks_mentioning(uid_b, [eb.id], 5)
        assert [h.text for h in hits_a] == ["用户A的正文"]
        assert [h.text for h in hits_b] == ["用户B的正文"]

        # 混合检索同样按用户隔离
        seen_a = {h.text for h in await store.search_chunks(uid_a, None, "正文", None, 10)}
        seen_b = {h.text for h in await store.search_chunks(uid_b, None, "正文", None, 10)}
        assert seen_a == {"用户A的正文"}
        assert seen_b == {"用户B的正文"}
    finally:
        for u in (uid_a, uid_b):
            await driver.execute_query(
                "MATCH (n) WHERE n.user_id = $uid DETACH DELETE n", {"uid": u})
