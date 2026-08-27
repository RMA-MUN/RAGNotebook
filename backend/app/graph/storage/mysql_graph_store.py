"""MySQL GraphStore 实现（SQLAlchemy 异步 ORM）。

方法接收外部注入的 AsyncSession：复用调用方事务；测试注入 SQLite 会话。
"""
import uuid
from datetime import datetime

from sqlalchemy import delete, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.graph.schemas.graph import (
    Entity,
    EntityIn,
    EntityNoteLink,
    EntityType,
    GraphEdge,
    GraphNode,
    GraphView,
    Relation,
    RelationIn,
    TypeIn,
)
from app.graph.storage.graph_store import GraphStore
from app.models.graph import (
    GraphDoc,
    GraphEntity,
    GraphEntityNote,
    GraphEntityType,
    GraphNoteEdge,
    GraphRelation,
)

SEED_TYPE_COLORS = {"person": "#E4572E", "tech": "#1F6C9F", "concept": "#2A9D8F",
                    "org": "#E9C46A", "place": "#9B5DE5", "project": "#F4A261", "event": "#D90429"}


async def _seed_system_types(session: AsyncSession):
    """惰性写入系统预置类型（person/tech/concept/org/place/project/event）。"""
    from sqlalchemy import func, select
    count = (await session.execute(
        select(func.count()).select_from(GraphEntityType).where(GraphEntityType.is_system.is_(True))
    )).scalar_one()
    if count:
        return
    for name, disp in (("person", "人物"), ("tech", "技术/工具"), ("concept", "概念"),
                       ("org", "组织"), ("place", "地点"), ("project", "项目"), ("event", "事件")):
        session.add(GraphEntityType(
            id=str(uuid.uuid4()), user_id=None, name=name, display_name=disp,
            color=SEED_TYPE_COLORS[name], is_system=True))


def _entity_to_schema(row: GraphEntity) -> Entity:
    return Entity(id=row.id, user_id=row.user_id, name=row.name, display_name=row.display_name,
                  type_id=row.type_id, description=row.description,
                  aliases=row.aliases or [], confidence=row.confidence,
                  source_note_ids=row.source_note_ids or [])


class MySQLGraphStore(GraphStore):
    def __init__(self, session: AsyncSession):
        self.session = session

    # ---- 实体 ----
    async def upsert_entity(self, user_id: str, entity: EntityIn) -> Entity:
        stmt = select(GraphEntity).where(GraphEntity.user_id == user_id, GraphEntity.name == entity.name)
        row = (await self.session.execute(stmt)).scalar_one_or_none()
        if row is None:
            # 别名命中：已有实体 aliases 包含本名 → 并入
            alias_hit = (await self.session.execute(
                select(GraphEntity).where(GraphEntity.user_id == user_id)
            )).scalars().all()
            for cand in alias_hit:
                if entity.name in (cand.aliases or []):
                    row = cand
                    break
        if row is None:
            row = GraphEntity(id=str(uuid.uuid4()), user_id=user_id, name=entity.name,
                              display_name=entity.display_name or entity.name,
                              type_id=entity.type_id, description=entity.description,
                              aliases=entity.aliases, confidence=entity.confidence,
                              source_note_ids=entity.source_note_ids)
            self.session.add(row)
        else:
            row.display_name = entity.display_name or row.display_name
            if entity.type_id:
                row.type_id = entity.type_id
            if entity.description:
                row.description = entity.description
            row.aliases = list(dict.fromkeys((row.aliases or []) + entity.aliases))
            row.confidence = max(row.confidence, entity.confidence)
            row.source_note_ids = list(dict.fromkeys((row.source_note_ids or []) + entity.source_note_ids))
        await self.session.flush()
        return _entity_to_schema(row)

    async def get_entity(self, user_id: str, entity_id: str) -> Entity | None:
        row = (await self.session.execute(
            select(GraphEntity).where(GraphEntity.id == entity_id, GraphEntity.user_id == user_id)
        )).scalar_one_or_none()
        return _entity_to_schema(row) if row else None

    async def search_entities(self, user_id: str, query: str, limit: int) -> list[Entity]:
        like = f"%{query}%"
        rows = (await self.session.execute(
            select(GraphEntity)
            .where(GraphEntity.user_id == user_id,
                   or_(GraphEntity.name.like(like), GraphEntity.display_name.like(like)))
            .limit(limit)
        )).scalars().all()
        return [_entity_to_schema(r) for r in rows]

    async def delete_entity(self, user_id: str, entity_id: str) -> None:
        await self.session.execute(delete(GraphRelation).where(
            GraphRelation.user_id == user_id,
            or_(GraphRelation.source_id == entity_id, GraphRelation.target_id == entity_id)))
        await self.session.execute(delete(GraphEntityNote).where(
            GraphEntityNote.user_id == user_id, GraphEntityNote.entity_id == entity_id))
        await self.session.execute(delete(GraphEntity).where(
            GraphEntity.user_id == user_id, GraphEntity.id == entity_id))

    async def merge_entities(self, user_id: str, target_id: str, source_id: str) -> Entity:
        target = (await self.session.execute(
            select(GraphEntity).where(GraphEntity.id == target_id, GraphEntity.user_id == user_id)
        )).scalar_one_or_none()
        source = (await self.session.execute(
            select(GraphEntity).where(GraphEntity.id == source_id, GraphEntity.user_id == user_id)
        )).scalar_one_or_none()
        if not target or not source:
            raise ValueError("合并目标或源实体不存在")
        # 关系全部重定向到目标
        for rel in (await self.session.execute(
                select(GraphRelation).where(GraphRelation.user_id == user_id,
                                           or_(GraphRelation.source_id == source_id,
                                               GraphRelation.target_id == source_id)))).scalars().all():
            if rel.source_id == source_id:
                rel.source_id = target_id
            if rel.target_id == source_id:
                rel.target_id = target_id
        # 实体-笔记关联重定向 + 合并别名
        for en in (await self.session.execute(
                select(GraphEntityNote).where(GraphEntityNote.user_id == user_id,
                                              GraphEntityNote.entity_id == source_id))).scalars().all():
            en.entity_id = target_id
        target.aliases = list(dict.fromkeys((target.aliases or []) + (source.aliases or [])))
        if not target.description and source.description:
            target.description = source.description
        target.confidence = max(target.confidence, source.confidence)
        await self.session.delete(source)
        await self.session.flush()
        return _entity_to_schema(target)

    # ---- 关系 ----
    async def create_relation(self, user_id: str, rel: RelationIn) -> Relation:
        row = GraphRelation(id=str(uuid.uuid4()), user_id=user_id, source_id=rel.source_id,
                            target_id=rel.target_id, relation_type=rel.relation_type,
                            properties=rel.properties, confidence=rel.confidence)
        self.session.add(row)
        await self.session.flush()
        return Relation(id=row.id, user_id=user_id, source_id=row.source_id, target_id=row.target_id,
                        relation_type=row.relation_type, properties=row.properties or {},
                        confidence=row.confidence)

    async def delete_relation(self, user_id: str, relation_id: str) -> None:
        await self.session.execute(delete(GraphRelation).where(
            GraphRelation.user_id == user_id, GraphRelation.id == relation_id))

    # ---- 查询 ----
    async def get_neighbors(self, user_id: str, entity_id: str, depth: int) -> GraphView:
        depth = max(1, min(depth, 3))
        ids = {entity_id}
        cur = {entity_id}
        for _ in range(depth):
            rels = (await self.session.execute(
                select(GraphRelation).where(GraphRelation.user_id == user_id,
                                           or_(GraphRelation.source_id.in_(cur),
                                               GraphRelation.target_id.in_(cur))))).scalars().all()
            nxt = set()
            for r in rels:
                nxt.add(r.source_id)
                nxt.add(r.target_id)
            cur = nxt - ids
            ids |= nxt
        entities = (await self.session.execute(
            select(GraphEntity).where(GraphEntity.user_id == user_id, GraphEntity.id.in_(ids)))).scalars().all()
        rels = (await self.session.execute(
            select(GraphRelation).where(GraphRelation.user_id == user_id,
                                       or_(GraphRelation.source_id.in_(ids),
                                           GraphRelation.target_id.in_(ids))))).scalars().all()
        nodes = [GraphNode(id=e.id, label=e.display_name or e.name, node_type="entity",
                           entity_type_id=e.type_id) for e in entities]
        edges = [GraphEdge(id=r.id, source=r.source_id, target=r.target_id, kind="relation",
                           relation_type=r.relation_type) for r in rels]
        return GraphView(nodes=nodes, edges=edges)

    async def get_note_graph(self, user_id: str, note_id: str) -> GraphView:
        edges = (await self.session.execute(
            select(GraphNoteEdge).where(GraphNoteEdge.user_id == user_id,
                                       or_(GraphNoteEdge.source_note_id == note_id,
                                           GraphNoteEdge.target_note_id == note_id)))).scalars().all()
        note_ids = {e.source_note_id for e in edges} | {e.target_note_id for e in edges} | {note_id}
        ens = (await self.session.execute(
            select(GraphEntityNote).where(GraphEntityNote.user_id == user_id,
                                          GraphEntityNote.note_id.in_(note_ids)))).scalars().all()
        entity_ids = {en.entity_id for en in ens}
        entities = (await self.session.execute(
            select(GraphEntity).where(GraphEntity.user_id == user_id, GraphEntity.id.in_(entity_ids)))).scalars().all()
        rels = (await self.session.execute(
            select(GraphRelation).where(GraphRelation.user_id == user_id,
                                       or_(GraphRelation.source_id.in_(entity_ids),
                                           GraphRelation.target_id.in_(entity_ids))))).scalars().all()
        nodes = [GraphNode(id=n, label="笔记", node_type="note") for n in note_ids]
        nodes += [GraphNode(id=e.id, label=e.display_name or e.name, node_type="entity",
                            entity_type_id=e.type_id) for e in entities]
        edges = [GraphEdge(id=ed.id, source=ed.source_note_id, target=ed.target_note_id,
                           kind=ed.kind) for ed in edges]
        edges += [GraphEdge(id=en.id, source=en.note_id, target=en.entity_id, kind="relation",
                            relation_type="提及") for en in ens]
        edges += [GraphEdge(id=r.id, source=r.source_id, target=r.target_id, kind="relation",
                            relation_type=r.relation_type) for r in rels]
        return GraphView(nodes=nodes, edges=edges)

    async def get_entity_notes(self, user_id: str, entity_id: str) -> list[EntityNoteLink]:
        rows = (await self.session.execute(
            select(GraphEntityNote).where(GraphEntityNote.user_id == user_id,
                                          GraphEntityNote.entity_id == entity_id))).scalars().all()
        # 文档关联行（note_id 存 md5）补上文件名，前端可区分展示
        doc_names: dict[str, str] = {}
        doc_ids = [r.note_id for r in rows if r.source_type == "doc"]
        if doc_ids:
            docs = (await self.session.execute(
                select(GraphDoc).where(GraphDoc.user_id == user_id,
                                       GraphDoc.id.in_(doc_ids)))).scalars().all()
            doc_names = {d.id: d.filename for d in docs}
        return [EntityNoteLink(entity_id=r.entity_id, note_id=r.note_id,
                               source_type=r.source_type, source_name=doc_names.get(r.note_id),
                               mention_count=r.mention_count, context=r.context or []) for r in rows]

    async def get_overview(self, user_id: str, type_ids: list[str] | None, limit: int) -> GraphView:
        await _seed_system_types(self.session)
        stmt = select(GraphEntity).where(GraphEntity.user_id == user_id)
        if type_ids:
            stmt = stmt.where(GraphEntity.type_id.in_(type_ids))
        entities = (await self.session.execute(stmt.limit(limit))).scalars().all()
        eids = [e.id for e in entities]
        rels = []
        if eids:
            rels = (await self.session.execute(
                select(GraphRelation).where(GraphRelation.user_id == user_id,
                                           or_(GraphRelation.source_id.in_(eids),
                                               GraphRelation.target_id.in_(eids))))).scalars().all()
        # 笔记提及（source_type 非 doc）：驱动笔记节点与 wiki 边（双链自动生长可见性）
        note_ens = []
        if eids:
            note_ens = (await self.session.execute(
                select(GraphEntityNote).where(GraphEntityNote.user_id == user_id,
                                              GraphEntityNote.entity_id.in_(eids),
                                              or_(GraphEntityNote.source_type.is_(None),
                                                  GraphEntityNote.source_type != "doc")))).scalars().all()
        note_ids = {en.note_id for en in note_ens}
        wedges = []
        if note_ids:
            wedges = (await self.session.execute(
                select(GraphNoteEdge).where(GraphNoteEdge.user_id == user_id,
                                           or_(GraphNoteEdge.source_note_id.in_(note_ids),
                                               GraphNoteEdge.target_note_id.in_(note_ids))))).scalars().all()
        # wiki 边另一端可能是不含实体关联的笔记（仅被双链引用）——补齐节点，避免悬空边
        note_ids |= {w.source_note_id for w in wedges} | {w.target_note_id for w in wedges}
        # 文档提及（source_type='doc'）+ 文档节点（graph_docs 提供 id=md5 与文件名）
        doc_ens = []
        if eids:
            doc_ens = (await self.session.execute(
                select(GraphEntityNote).where(GraphEntityNote.user_id == user_id,
                                              GraphEntityNote.entity_id.in_(eids),
                                              GraphEntityNote.source_type == "doc"))).scalars().all()
        docs = (await self.session.execute(
            select(GraphDoc).where(GraphDoc.user_id == user_id))).scalars().all()
        nodes = [GraphNode(id=e.id, label=e.display_name or e.name, node_type="entity",
                           entity_type_id=e.type_id) for e in entities]
        nodes += [GraphNode(id=n, label="笔记", node_type="note") for n in note_ids]
        nodes += [GraphNode(id=d.id, label=d.filename, node_type="doc") for d in docs]
        edges = [GraphEdge(id=r.id, source=r.source_id, target=r.target_id, kind="relation",
                           relation_type=r.relation_type) for r in rels]
        edges += [GraphEdge(id=en.id, source=en.note_id, target=en.entity_id, kind="relation",
                            relation_type="提及") for en in note_ens]
        edges += [GraphEdge(id=en.id, source=en.note_id, target=en.entity_id, kind="relation",
                            relation_type="提及") for en in doc_ens]
        edges += [GraphEdge(id=w.id, source=w.source_note_id, target=w.target_note_id, kind=w.kind)
                  for w in wedges]
        return GraphView(nodes=nodes, edges=edges)

    async def get_doc_graph(self, user_id: str, doc_id: str) -> GraphView:
        """返回文档子图：文档节点 + 其关联实体及实体间关系（与 get_note_graph 对称）。"""
        doc = (await self.session.execute(
            select(GraphDoc).where(GraphDoc.user_id == user_id, GraphDoc.id == doc_id))).scalar_one_or_none()
        ens = (await self.session.execute(
            select(GraphEntityNote).where(GraphEntityNote.user_id == user_id,
                                          GraphEntityNote.note_id == doc_id,
                                          GraphEntityNote.source_type == "doc"))).scalars().all()
        entity_ids = {en.entity_id for en in ens}
        entities = []
        if entity_ids:
            entities = (await self.session.execute(
                select(GraphEntity).where(GraphEntity.user_id == user_id,
                                          GraphEntity.id.in_(entity_ids)))).scalars().all()
        rels = []
        if entity_ids:
            rels = (await self.session.execute(
                select(GraphRelation).where(GraphRelation.user_id == user_id,
                                           or_(GraphRelation.source_id.in_(entity_ids),
                                               GraphRelation.target_id.in_(entity_ids))))).scalars().all()
        nodes = [GraphNode(id=doc_id, label=doc.filename if doc else "文档", node_type="doc")]
        nodes += [GraphNode(id=e.id, label=e.display_name or e.name, node_type="entity",
                            entity_type_id=e.type_id) for e in entities]
        edges = [GraphEdge(id=en.id, source=en.note_id, target=en.entity_id, kind="relation",
                           relation_type="提及") for en in ens]
        edges += [GraphEdge(id=r.id, source=r.source_id, target=r.target_id, kind="relation",
                            relation_type=r.relation_type) for r in rels]
        return GraphView(nodes=nodes, edges=edges)

    # ---- 类型 ----
    async def list_types(self, user_id: str) -> list[EntityType]:
        await _seed_system_types(self.session)
        rows = (await self.session.execute(
            select(GraphEntityType).where(or_(GraphEntityType.user_id.is_(None),
                                              GraphEntityType.user_id == user_id)))).scalars().all()
        return [EntityType(id=r.id, user_id=r.user_id, name=r.name, display_name=r.display_name,
                           color=r.color, icon=r.icon, is_system=r.is_system) for r in rows]

    async def upsert_type(self, user_id: str, type_in: TypeIn) -> EntityType:
        stmt = select(GraphEntityType).where(
            GraphEntityType.user_id == user_id, GraphEntityType.name == type_in.name)
        row = (await self.session.execute(stmt)).scalar_one_or_none()
        if row is None:
            row = GraphEntityType(id=str(uuid.uuid4()), user_id=user_id, name=type_in.name,
                                  display_name=type_in.display_name, color=type_in.color,
                                  icon=type_in.icon, is_system=False)
            self.session.add(row)
        else:
            row.display_name = type_in.display_name
            row.color = type_in.color
            row.icon = type_in.icon
        await self.session.flush()
        return EntityType(id=row.id, user_id=user_id, name=row.name, display_name=row.display_name,
                          color=row.color, icon=row.icon, is_system=row.is_system)

    async def delete_type(self, user_id: str, type_id: str) -> None:
        # 实体 type_id 置空降级未分类，不级联删实体
        await self.session.execute(update(GraphEntity).where(
            GraphEntity.user_id == user_id, GraphEntity.type_id == type_id).values(type_id=None))
        await self.session.execute(delete(GraphEntityType).where(
            GraphEntityType.user_id == user_id, GraphEntityType.id == type_id))