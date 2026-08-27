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
    async def upsert_entity(self, user_id: str, entity: EntityIn) -> Entity:
        """按名称或别名去重写入实体；已存在则合并：aliases/source_note_ids 取去重并集、confidence 取 max，type_id/description 非空才覆盖。"""

    @abstractmethod
    async def get_entity(self, user_id: str, entity_id: str) -> Entity | None:
        """按 id 精确查找实体（限定 user_id），不存在返回 None。"""

    @abstractmethod
    async def search_entities(self, user_id: str, query: str, limit: int) -> list[Entity]:
        """按 name/display_name 模糊匹配实体，返回至多 limit 条。"""

    @abstractmethod
    async def delete_entity(self, user_id: str, entity_id: str) -> None:
        """删除实体，并级联删除其所有关系与实体-笔记关联（不删除笔记本身）。"""

    @abstractmethod
    async def merge_entities(self, user_id: str, target_id: str, source_id: str) -> Entity:
        """将 source 合并进 target：关系/笔记关联重定向到 target、别名并入、删除 source；任一侧不存在抛 ValueError。"""

    # 关系
    @abstractmethod
    async def create_relation(self, user_id: str, rel: RelationIn) -> Relation:
        """创建一条实体关系，返回带 id 的 Relation。"""

    @abstractmethod
    async def delete_relation(self, user_id: str, relation_id: str) -> None:
        """按 id 删除关系（限定 user_id）。"""

    # 查询
    @abstractmethod
    async def get_neighbors(self, user_id: str, entity_id: str, depth: int) -> GraphView:
        """返回以实体为中心 depth 跳内的实体与关系子图；depth 按 1~3 截断。"""

    @abstractmethod
    async def get_note_graph(self, user_id: str, note_id: str) -> GraphView:
        """返回笔记的双链图：笔记节点、笔记提及的实体及实体间关系。"""

    @abstractmethod
    async def get_entity_notes(self, user_id: str, entity_id: str) -> list[EntityNoteLink]:
        """返回提及该实体的笔记列表（含提及次数与上下文片段）。"""

    @abstractmethod
    async def get_overview(self, user_id: str, type_ids: list[str] | None, limit: int) -> GraphView:
        """返回实体总览图：可按 type_ids 过滤；含笔记节点与 wiki 双链边，避免悬空边。"""

    # 类型
    @abstractmethod
    async def list_types(self, user_id: str) -> list[EntityType]:
        """返回系统预置类型与用户自定义类型（系统类型不存在时先种入）。"""

    @abstractmethod
    async def upsert_type(self, user_id: str, type_in: TypeIn) -> EntityType:
        """按 user_id+name 去重写入类型；已存在则更新 display_name/color/icon。"""

    @abstractmethod
    async def delete_type(self, user_id: str, type_id: str) -> None:
        """删除类型，并将引用它的实体 type_id 置空（降级未分类，不级联删实体）。"""