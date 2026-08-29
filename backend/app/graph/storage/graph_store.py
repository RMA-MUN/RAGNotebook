"""GraphStore 存储层抽象接口。

service 层只依赖本接口；通过 storage/__init__.py 的 get_graph_store() 工厂切换实现。
基础 15 个方法为实体/关系/画布语义，MySQL 与 Neo4j 双实现；
"Chunk 检索"扩展方法仅 Neo4jGraphStore 提供（MySQL 无向量/全文能力），默认抛 NotImplementedError。
"""
from abc import ABC, abstractmethod

from app.graph.schemas.graph import (
    ChunkHit,
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
    """图谱存储统一接口：方法 docstring 即行为契约，MySQL/Neo4j 双实现必须对齐（测试双跑保证）。"""
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
    async def get_doc_graph(self, user_id: str, doc_id: str) -> GraphView:
        """返回知识库文档的子图：文档节点（id=md5）、其关联实体及实体间关系。"""

    @abstractmethod
    async def get_entity_notes(self, user_id: str, entity_id: str) -> list[EntityNoteLink]:
        """返回提及该实体的笔记/文档列表（含提及次数与上下文片段；source_type 区分 note/doc，文档带 source_name=文件名）。"""

    @abstractmethod
    async def get_overview(self, user_id: str, type_ids: list[str] | None, limit: int) -> GraphView:
        """返回实体总览图：可按 type_ids 过滤；含笔记节点、文档节点与 wiki 双链边，避免悬空边。"""

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

    # ---- Chunk 检索扩展（仅 Neo4jGraphStore 实现；MySQL 无向量/全文能力） ----

    async def ensure_source_node(self, user_id: str, source_type: str, source_id: str, title: str) -> None:
        """确保 Note/Doc 源节点存在（note→Note{title}，doc→Doc{filename}）。"""
        raise NotImplementedError("当前 GraphStore 实现不支持 Chunk 存储")

    async def upsert_chunks(self, user_id: str, source_type: str, source_id: str, source_name: str,
                            chunks: list[dict]) -> None:
        """按 (kind, source_id, chunk_index) 幂等写入 Chunk 节点（含 text 与 embedding）。

        chunks 每项：{chunk_index, text, embedding, page?, image_paths?}。
        """
        raise NotImplementedError("当前 GraphStore 实现不支持 Chunk 存储")

    async def delete_chunks_by_source(self, user_id: str, source_type: str, source_id: str) -> None:
        """删除某来源的全部 Chunk 节点（连带 Chunk 级 MENTIONS 边）。"""
        raise NotImplementedError("当前 GraphStore 实现不支持 Chunk 存储")

    async def set_source_mentions(self, user_id: str, source_type: str, source_id: str,
                                  links: list[dict]) -> None:
        """先清后插来源级 MENTIONS 边（Note/Doc→Entity，{mention_count, context}）。"""
        raise NotImplementedError("当前 GraphStore 实现不支持 Chunk 存储")

    async def set_chunk_mentions(self, user_id: str, source_type: str, source_id: str,
                                 links: list[dict]) -> None:
        """先清后插 Chunk 级 MENTIONS 边（Chunk→Entity）。

        links 每项：{entity_id, chunk_indexes: [int]}，按 (kind, source_id, chunk_index) 定位 Chunk。
        """
        raise NotImplementedError("当前 GraphStore 实现不支持 Chunk 存储")

    async def set_relations_from_source(self, user_id: str, source_type: str, source_id: str,
                                        rels: list[dict]) -> None:
        """先清后插带溯源的实体关系（RELATES_TO {source_id, source_type}）。"""
        raise NotImplementedError("当前 GraphStore 实现不支持 Chunk 存储")

    async def set_note_wiki_edges(self, user_id: str, note_id: str, links: list[dict]) -> None:
        """先清后插笔记双链出边（links: [{target_note_id, kind}]；单向存储、查询双向匹配）。"""
        raise NotImplementedError("当前 GraphStore 实现不支持 Chunk 存储")

    async def search_chunks(self, user_id: str, query_embedding: list[float] | None,
                            text_query: str | None, kinds: list[str] | None, limit: int) -> list[ChunkHit]:
        """种子检索：向量 + 全文（RRF 融合）返回 top chunk。"""
        raise NotImplementedError("当前 GraphStore 实现不支持 Chunk 检索")

    async def get_chunks_mentioning(self, user_id: str, entity_ids: list[str], limit: int) -> list[ChunkHit]:
        """返回提及给定实体的 chunk（图扩展证据）。"""
        raise NotImplementedError("当前 GraphStore 实现不支持 Chunk 检索")

    async def clear_source_data(self, user_id: str, source_type: str, source_id: str) -> list[str]:
        """删除源节点/Chunk/溯源关系，返回删除前曾关联的实体 id（供孤儿清扫）。"""
        raise NotImplementedError("当前 GraphStore 实现不支持 Chunk 存储")

    async def sweep_orphan_entities(self, user_id: str, candidate_entity_ids: list[str],
                                    removed_source_ids: list[str]) -> None:
        """孤儿清扫：摘除 source_note_ids 中被删来源；无剩余来源关联且引用为空的实体级联删除。"""
        raise NotImplementedError("当前 GraphStore 实现不支持 Chunk 存储")

    async def clear_all_docs(self, user_id: str) -> None:
        """清空用户全部文档图谱（Doc 节点/doc Chunk/doc 溯源关系），并清扫孤儿实体。"""
        raise NotImplementedError("当前 GraphStore 实现不支持 Chunk 存储")