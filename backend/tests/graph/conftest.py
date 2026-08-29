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


@pytest.fixture(scope="session", autouse=True)
async def _cleanup_neo4j_schema():
    """Neo4j 集成测试会把 Schema 建到 NEO4J_TEST_URI 指向的实例上（通常是本地开发库）。

    会话结束后删除全部测试创建的约束/索引，避免残留（尤其是测试假模型的 8 维向量索引
    会与真实嵌入模型维度冲突，导致 ensure_graph_schema 拒绝服务）。
    真实应用启动/回填脚本会以正确参数幂等重建。
    """
    yield
    uri = os.getenv("NEO4J_TEST_URI")
    if not uri:
        return
    from neo4j import AsyncGraphDatabase

    driver = AsyncGraphDatabase.driver(
        uri, auth=("neo4j", os.getenv("NEO4J_TEST_PASSWORD", "neo4j2026")))
    try:
        for stmt in _DROP_SCHEMA_STMTS:
            await driver.execute_query(stmt)
    finally:
        await driver.close()
