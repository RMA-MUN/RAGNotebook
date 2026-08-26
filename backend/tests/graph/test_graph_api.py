import pytest

from app.models.graph import (
    GraphEntity,
    GraphEntityType,
    GraphRelation,
)


@pytest.mark.asyncio
async def test_overview_and_entity_crud(client):
    # 建一个实体
    r = await client.post("/api/graph/entities", json={
        "name": "Python", "display_name": "Python", "confidence": 0.9})
    assert r.status_code == 200
    eid = r.json()["data"]["id"]

    r = await client.get("/api/graph/entity/" + eid)
    assert r.status_code == 200
    assert r.json()["data"]["name"] == "Python"

    r = await client.get("/api/graph/overview?limit=20")
    assert r.status_code == 200
    assert any(n["id"] == eid for n in r.json()["data"]["nodes"])


@pytest.mark.asyncio
async def test_merge_entities(client):
    a = (await client.post("/api/graph/entities", json={"name": "A"})).json()["data"]
    b = (await client.post("/api/graph/entities", json={"name": "B"})).json()["data"]
    await client.post("/api/graph/relations", json={
        "source_id": a["id"], "target_id": b["id"], "relation_type": "属于"})
    r = await client.post("/api/graph/entities/merge", json={
        "target_id": b["id"], "source_id": a["id"]})
    assert r.status_code == 200
    assert r.json()["data"]["id"] == b["id"]


@pytest.mark.asyncio
async def test_merge_self_returns_entity_without_deleting(client):
    # 自合并守卫：target_id == source_id 不删行，实体保留
    e = (await client.post("/api/graph/entities", json={"name": "Solo"})).json()["data"]
    r = await client.post("/api/graph/entities/merge", json={
        "target_id": e["id"], "source_id": e["id"]})
    assert r.status_code == 200
    assert r.json()["data"]["id"] == e["id"]
    r = await client.get("/api/graph/entity/" + e["id"])
    assert r.status_code == 200
    assert r.json()["data"]["name"] == "Solo"


@pytest.mark.asyncio
async def test_search_returns_entity_and_note_groups(client):
    await client.post("/api/graph/entities", json={"name": "FastAPI", "aliases": ["fastapi"]})
    r = await client.get("/api/graph/search", params={"q": "fastapi"})
    assert r.status_code == 200
    assert any(e["name"] == "FastAPI" for e in r.json()["data"]["entities"])


@pytest.mark.asyncio
async def test_types_crud_api(client):
    r = await client.post("/api/graph/types", json={
        "name": "org", "display_name": "组织", "color": "#00FF00"})
    assert r.status_code == 200
    tid = r.json()["data"]["id"]
    r = await client.get("/api/graph/types")
    assert any(t["id"] == tid for t in r.json()["data"])


@pytest.mark.asyncio
async def test_extract_logs_endpoint(client):
    r = await client.get("/api/graph/extract-logs")
    assert r.status_code == 200