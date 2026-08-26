"""GraphStore 存储层实现包。

service 层一律通过 get_graph_store(session) 获取实现，
未来迁移 Neo4j 时只需把本工厂改为返回 Neo4jGraphStore。
"""
from sqlalchemy.ext.asyncio import AsyncSession

from app.graph.storage.graph_store import GraphStore
from app.graph.storage.mysql_graph_store import MySQLGraphStore


def get_graph_store(session: AsyncSession) -> GraphStore:
    """返回当前配置的 GraphStore 实现（当前唯一实现为 MySQL）。"""
    return MySQLGraphStore(session)