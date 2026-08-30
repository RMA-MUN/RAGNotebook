"""tests/graph 包级 fixtures。"""
import os

import pytest

# ensure_graph_schema 创建的全部 Schema 对象（测试结束后逐一删除）
_DROP_SCHEMA_STMTS = (
    "DROP INDEX chunk_embedding_index IF EXISTS",
    "DROP INDEX chunk_text_index IF EXISTS",
    "DROP INDEX chunk_user_index IF EXISTS",
    "DROP INDEX chunk_source_index IF EXISTS",
    "DROP INDEX chunk_source_user_index IF EXISTS",
    "DROP INDEX doc_user_index IF EXISTS",
    "DROP INDEX entity_user_index IF EXISTS",
    "DROP CONSTRAINT chunk_id_unique IF EXISTS",
    "DROP CONSTRAINT entity_id_unique IF EXISTS",
    "DROP CONSTRAINT entity_user_name_unique IF EXISTS",
    "DROP CONSTRAINT entity_type_id_unique IF EXISTS",
    "DROP CONSTRAINT note_id_unique IF EXISTS",
    "DROP CONSTRAINT doc_id_unique IF EXISTS",
)


@pytest.fixture
async def _cleanup():
    """每个用例结束后清理测试产生的 Neo4j 子图（pipe- 前缀用户 + API 测试的固定用户）。"""
    yield
    from tests.fakes import TEST_USER_ID

    from app.graph.storage import neo4j_client

    driver = neo4j_client.get_neo4j_driver()
    await driver.execute_query(
        "MATCH (n) WHERE n.user_id STARTS WITH 'pipe-' OR n.user_id = $uid "
        "OR n.user_id IN ['u1', $tuid] DETACH DELETE n",
        {"uid": TEST_USER_ID, "tuid": TEST_USER_ID})


async def _drop_schema_objects(uri: str, password: str) -> None:
    from neo4j import AsyncGraphDatabase

    driver = AsyncGraphDatabase.driver(uri, auth=("neo4j", password))
    try:
        for stmt in _DROP_SCHEMA_STMTS:
            await driver.execute_query(stmt)
    finally:
        await driver.close()


@pytest.fixture(scope="session", autouse=True)
async def _cleanup_neo4j_schema():
    """Neo4j 集成测试会把 Schema 建到 NEO4J_TEST_URI 指向的实例上（通常是本地开发库）。

    会话开始前先删除既有约束/索引：本地库可能已有真实嵌入维度（如 1024）的向量索引，
    与测试假模型的 8 维探针冲突，ensure_graph_schema 会按设计拒绝服务。
    会话结束后再次删除全部测试创建的 Schema 对象，避免残留；
    真实应用启动/回填脚本会以正确参数幂等重建。
    """
    uri = os.getenv("NEO4J_TEST_URI")
    if not uri:
        yield
        return
    password = os.getenv("NEO4J_TEST_PASSWORD", "neo4j2026")
    await _drop_schema_objects(uri, password)
    yield
    await _drop_schema_objects(uri, password)
