import pytest
from sqlalchemy import inspect, select

from app.models.graph import (
    GraphEntity,
    GraphEntityNote,
    GraphEntityType,
    GraphExtractLog,
    GraphNoteEdge,
    GraphRelation,
)


@pytest.mark.asyncio
async def test_create_all_registers_six_tables(db_engine):
    async with db_engine.begin() as conn:
        tables = await conn.run_sync(lambda sc: inspect(sc).get_table_names())
    for name in (
        "graph_entity_types",
        "graph_entities",
        "graph_relations",
        "graph_entity_notes",
        "graph_note_edges",
        "graph_extract_logs",
    ):
        assert name in tables


@pytest.mark.asyncio
async def test_entity_unique_on_user_and_name(db_session):
    db_session.add(GraphEntity(user_id="u1", name="Python", display_name="Python", confidence=0.9))
    db_session.add(GraphEntity(user_id="u1", name="Python", display_name="Python", confidence=0.5))
    with pytest.raises(Exception):
        await db_session.commit()


@pytest.mark.asyncio
async def test_extract_log_unique_on_user_and_note(db_session):
    db_session.add(GraphExtractLog(user_id="u1", note_id="n1", content_hash="a", status="success"))
    db_session.add(GraphExtractLog(user_id="u1", note_id="n1", content_hash="b", status="pending"))
    with pytest.raises(Exception):
        await db_session.commit()
