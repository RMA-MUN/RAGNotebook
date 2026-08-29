"""GraphStore 存储层实现包。

service 层一律通过 get_graph_store(session) 获取实现：
- 配置了 NEO4J_URI → Neo4jGraphStore（自管驱动连接，session 参数被忽略）；
- 未配置 → MySQLGraphStore（接收外部注入的 AsyncSession；测试走此路径）。
"""
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.failed_response import settings
from app.graph.storage.graph_store import GraphStore
from app.graph.storage.mysql_graph_store import MySQLGraphStore


def get_graph_store(session: AsyncSession | None = None) -> GraphStore:
    """返回当前配置的 GraphStore 实现（Neo4j 优先，未配置回落 MySQL）。"""
    if settings.NEO4J_URI:
        from app.graph.storage.neo4j_graph_store import Neo4jGraphStore

        return Neo4jGraphStore()
    return MySQLGraphStore(session)
