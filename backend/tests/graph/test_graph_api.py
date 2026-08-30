"""图谱 API 集成测试（需要真实 Neo4j）。

    NEO4J_TEST_URI=bolt://localhost:7687 uv run --extra dev pytest tests/graph/test_graph_api.py -v

复用 client fixture（SQLite + 假模型），store 走真实 Neo4j；每个用例结束后由包级
conftest 的 _cleanup 清理测试子图，会话结束删除测试建出的 Schema 对象。
"""
import os

import pytest

pytestmark = pytest.mark.skipif(
    not os.getenv("NEO4J_TEST_URI"), reason="需要真实 Neo4j（设 NEO4J_TEST_URI 启用）")


@pytest.fixture
def neo4j_env(monkeypatch):
    from app.core.failed_response import settings

    monkeypatch.setattr(settings, "NEO4J_URI", os.environ["NEO4J_TEST_URI"], raising=False)


@pytest.mark.asyncio
async def test_overview_and_entity_crud(client, neo4j_env, _cleanup):
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
async def test_merge_entities(client, neo4j_env, _cleanup):
    a = (await client.post("/api/graph/entities", json={"name": "A"})).json()["data"]
    b = (await client.post("/api/graph/entities", json={"name": "B"})).json()["data"]
    await client.post("/api/graph/relations", json={
        "source_id": a["id"], "target_id": b["id"], "relation_type": "属于"})
    r = await client.post("/api/graph/entities/merge", json={
        "target_id": b["id"], "source_id": a["id"]})
    assert r.status_code == 200
    assert r.json()["data"]["id"] == b["id"]


@pytest.mark.asyncio
async def test_merge_self_returns_entity_without_deleting(client, neo4j_env, _cleanup):
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
async def test_search_returns_entity_and_note_groups(client, neo4j_env, _cleanup):
    await client.post("/api/graph/entities", json={"name": "FastAPI", "aliases": ["fastapi"]})
    r = await client.get("/api/graph/search", params={"q": "fastapi"})
    assert r.status_code == 200
    assert any(e["name"] == "FastAPI" for e in r.json()["data"]["entities"])


@pytest.mark.asyncio
async def test_types_crud_api(client, neo4j_env, _cleanup):
    r = await client.post("/api/graph/types", json={
        "name": "org", "display_name": "组织", "color": "#00FF00"})
    assert r.status_code == 200
    tid = r.json()["data"]["id"]
    r = await client.get("/api/graph/types")
    assert any(t["id"] == tid for t in r.json()["data"])


@pytest.mark.asyncio
async def test_put_entity_preserves_unset_fields(client, neo4j_env, _cleanup):
    # 建类型 + 带 type_id/aliases/confidence 的实体
    tid = (await client.post("/api/graph/types", json={
        "name": "lang", "display_name": "语言", "color": "#FF0000"})).json()["data"]["id"]
    e = (await client.post("/api/graph/entities", json={
        "name": "Python", "type_id": tid, "aliases": ["py", "CPython"], "confidence": 0.9})).json()["data"]
    # 局部 PUT 只改 name，未传字段必须保留
    r = await client.put("/api/graph/entities/" + e["id"], json={"name": "Renamed"})
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["name"] == "Renamed"
    assert data["type_id"] == tid
    assert data["aliases"] == ["py", "CPython"]
    assert data["confidence"] == 0.9


@pytest.mark.asyncio
async def test_extract_logs_endpoint(client, neo4j_env):
    r = await client.get("/api/graph/extract-logs")
    assert r.status_code == 200
