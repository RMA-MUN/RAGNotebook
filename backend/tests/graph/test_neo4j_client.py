"""Neo4j 连接层测试。

单测不依赖真实 Neo4j；Schema/连通性集成测试用 NEO4J_TEST_URI 门控：
    NEO4J_TEST_URI=bolt://localhost:7687 uv run pytest tests/graph/test_neo4j_client.py -v
"""
import os

import pytest

from app.graph.storage import neo4j_client
from app.graph.storage.neo4j_client import neo4j_configured


def test_settings_has_neo4j_fields():
    from app.core.failed_response import settings

    assert hasattr(settings, "NEO4J_URI")
    assert hasattr(settings, "NEO4J_USER")
    assert hasattr(settings, "NEO4J_PASSWORD")


def test_driver_raises_when_unconfigured(monkeypatch):
    monkeypatch.setattr(neo4j_client.settings, "NEO4J_URI", "")
    # 清空单例，排除其它测试已创建驱动的影响
    monkeypatch.setattr(neo4j_client, "_driver", None)
    with pytest.raises(RuntimeError, match="NEO4J_URI"):
        neo4j_client.get_neo4j_driver()


def test_neo4j_configured_follows_uri(monkeypatch):
    monkeypatch.setattr(neo4j_client.settings, "NEO4J_URI", "")
    assert neo4j_configured() is False
    monkeypatch.setattr(neo4j_client.settings, "NEO4J_URI", "bolt://localhost:7687")
    assert neo4j_configured() is True


def test_probe_dims_caches(monkeypatch):
    calls = []

    class FakeEmbed:
        def embed_query(self, text):
            calls.append(text)
            return [0.1] * 8

    neo4j_client._probed_dims = None
    assert neo4j_client.probe_embedding_dims(FakeEmbed()) == 8
    assert neo4j_client.probe_embedding_dims(FakeEmbed()) == 8  # 第二次走缓存
    assert len(calls) == 1
    neo4j_client._probed_dims = None


@pytest.mark.skipif(not os.getenv("NEO4J_TEST_URI"), reason="需要真实 Neo4j（设 NEO4J_TEST_URI 启用）")
@pytest.mark.asyncio
async def test_ensure_graph_schema_idempotent(monkeypatch):
    from neo4j import AsyncGraphDatabase

    from app.graph.storage import neo4j_client

    # conftest 的 autouse fixture 会遮蔽 NEO4J_URI，这里按测试配置恢复
    monkeypatch.setattr(neo4j_client.settings, "NEO4J_URI", os.environ["NEO4J_TEST_URI"], raising=False)
    uri = os.environ["NEO4J_TEST_URI"]
    driver = AsyncGraphDatabase.driver(uri, auth=("neo4j", os.getenv("NEO4J_TEST_PASSWORD", "neo4j2026")))

    class _ProbeEmbed:
        def embed_query(self, text):
            return [0.0] * 8

    try:
        # 连跑两遍验证幂等
        await neo4j_client.ensure_graph_schema(_ProbeEmbed())
        await neo4j_client.ensure_graph_schema(_ProbeEmbed())

        result = await driver.execute_query(
            "SHOW INDEXES YIELD name, type WHERE name IN "
            "['chunk_text_index', 'chunk_embedding_index'] RETURN name, type"
        )
        names = {r["name"] for r in result.records}
        assert "chunk_text_index" in names
        assert "chunk_embedding_index" in names
    finally:
        await driver.close()
