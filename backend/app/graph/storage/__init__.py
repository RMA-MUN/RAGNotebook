"""GraphStore 存储层实现包。

service 层一律通过 get_graph_store(session) 获取实现：
图谱存储只有 Neo4j 单一实现（自管驱动连接，session 参数被忽略，保留以兼容既有调用方）；
Neo4j 未配置/不可用时由调用方降级（API 返回 503，主流程跳过图谱步骤）。
"""
from sqlalchemy.ext.asyncio import AsyncSession

from app.graph.storage.graph_store import GraphStore


def get_graph_store(session: AsyncSession | None = None) -> GraphStore:
    """返回 Neo4j 图谱存储实现。"""
    from app.graph.storage.neo4j_graph_store import Neo4jGraphStore

    return Neo4jGraphStore()
