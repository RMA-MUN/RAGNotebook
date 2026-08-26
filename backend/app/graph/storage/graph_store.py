"""GraphStore 存储层抽象接口。

service 层只依赖本接口；未来迁移 Neo4j 时新增 Neo4jGraphStore 实现，
并通过 storage/__init__.py 的 get_graph_store() 工厂切换，service 层零改动。
"""
from abc import ABC, abstractmethod

from app.graph.schemas.graph import (
    Entity,
    EntityIn,
    EntityNoteLink,
    EntityType,
    GraphView,
    Relation,
    RelationIn,
    TypeIn,
)


class GraphStore(ABC):
    # 实体
    @abstractmethod
    async def upsert_entity(self, user_id: str, entity: EntityIn) -> Entity: ...

    @abstractmethod
    async def get_entity(self, user_id: str, entity_id: str) -> Entity | None: ...

    @abstractmethod
    async def search_entities(self, user_id: str, query: str, limit: int) -> list[Entity]: ...

    @abstractmethod
    async def delete_entity(self, user_id: str, entity_id: str) -> None: ...

    @abstractmethod
    async def merge_entities(self, user_id: str, target_id: str, source_id: str) -> Entity: ...

    # 关系
    @abstractmethod
    async def create_relation(self, user_id: str, rel: RelationIn) -> Relation: ...

    @abstractmethod
    async def delete_relation(self, user_id: str, relation_id: str) -> None: ...

    # 查询
    @abstractmethod
    async def get_neighbors(self, user_id: str, entity_id: str, depth: int) -> GraphView: ...

    @abstractmethod
    async def get_note_graph(self, user_id: str, note_id: str) -> GraphView: ...

    @abstractmethod
    async def get_entity_notes(self, user_id: str, entity_id: str) -> list[EntityNoteLink]: ...

    @abstractmethod
    async def get_overview(self, user_id: str, type_ids: list[str] | None, limit: int) -> GraphView: ...

    # 类型
    @abstractmethod
    async def list_types(self, user_id: str) -> list[EntityType]: ...

    @abstractmethod
    async def upsert_type(self, user_id: str, type_in: TypeIn) -> EntityType: ...

    @abstractmethod
    async def delete_type(self, user_id: str, type_id: str) -> None: ...