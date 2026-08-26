import pytest

from app.graph.schemas.graph import EntityIn, TypeIn
from app.graph.storage.mysql_graph_store import MySQLGraphStore
from app.models.graph import (
    GraphEntity,
    GraphEntityNote,
    GraphEntityType,
    GraphExtractLog,
    GraphNoteEdge,
    GraphRelation,
)


async def _seed(session):
    t = GraphEntityType(id="t1", user_id=None, name="tech", display_name="技术/工具",
                        color="#1F6C9F", is_system=True)
    session.add(t)
    session.add(GraphEntity(id="e1", user_id="u1", name="Python", display_name="Python", type_id="t1"))
    session.add(GraphEntity(id="e2", user_id="u1", name="FastAPI", display_name="FastAPI", type_id="t1"))
    session.add(GraphEntity(id="e3", user_id="u1", name="Neo4j", display_name="Neo4j"))
    session.add(GraphRelation(id="r1", user_id="u1", source_id="e1", target_id="e2", relation_type="基于"))
    session.add(GraphNoteEdge(id="w1", user_id="u1", source_note_id="n1", target_note_id="n2", kind="wiki"))
    session.add(GraphEntityNote(id="en1", user_id="u1", entity_id="e1", note_id="n1", mention_count=2,
                                context=[{"snippet": "用 Python 写"}]))
    session.add(GraphExtractLog(id="log1", user_id="u1", note_id="n1", content_hash="h1", status="success"))


@pytest.mark.asyncio
async def test_upsert_entity_create_then_update(db_session):
    store = MySQLGraphStore(db_session)
    e = await store.upsert_entity("u1", EntityIn(name="Docker", display_name="Docker", confidence=0.8))
    assert e.name == "Docker"
    e2 = await store.upsert_entity("u1", EntityIn(name="Docker", display_name="Docker", confidence=0.9))
    assert e2.id == e.id  # 同名命中同一实体


@pytest.mark.asyncio
async def test_search_entities_matches_name_and_alias(db_session):
    await _seed(db_session)
    store = MySQLGraphStore(db_session)
    hits = await store.search_entities("u1", "python", limit=10)
    assert [e.name for e in hits] == ["Python"]


@pytest.mark.asyncio
async def test_get_neighbors_depth_1(db_session):
    await _seed(db_session)
    store = MySQLGraphStore(db_session)
    view = await store.get_neighbors("u1", "e1", depth=1)
    ids = {n.id for n in view.nodes}
    assert {"e1", "e2"} <= ids
    assert any(e.kind == "relation" for e in view.edges)


@pytest.mark.asyncio
async def test_get_note_graph_includes_entity_and_note_edges(db_session):
    await _seed(db_session)
    store = MySQLGraphStore(db_session)
    view = await store.get_note_graph("u1", "n1")
    assert {n.id for n in view.nodes} >= {"n1", "e1"}


@pytest.mark.asyncio
async def test_get_overview_filters_by_type(db_session):
    await _seed(db_session)
    store = MySQLGraphStore(db_session)
    view = await store.get_overview("u1", type_ids=["t1"], limit=20)
    assert {n.id for n in view.nodes if n.node_type == "entity"} == {"e1", "e2"}
    assert {n.id for n in view.nodes if n.node_type == "note"} == {"n1"}


@pytest.mark.asyncio
async def test_merge_entities_redirects_relations(db_session):
    await _seed(db_session)
    store = MySQLGraphStore(db_session)
    merged = await store.merge_entities("u1", "e2", "e1")
    assert merged.id == "e2"
    rel = (await db_session.execute(
        __import__("sqlalchemy").select(GraphRelation).where(GraphRelation.id == "r1"))).scalar_one()
    assert rel.source_id == "e2"


@pytest.mark.asyncio
async def test_types_crud(db_session):
    store = MySQLGraphStore(db_session)
    t = await store.upsert_type("u1", TypeIn(name="org", display_name="组织", color="#00FF00"))
    assert t.name == "org"
    types = await store.list_types("u1")
    assert any(x.name == "org" for x in types)