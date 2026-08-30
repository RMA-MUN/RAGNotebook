"""Neo4j GraphStore 实现（neo4j 官方 Python 驱动，异步）。

语义约定：
- 实体 upsert 按名称/别名去重、字段合并；merge_entities 关系/关联重定向；
- 画布三查询（note_graph/doc_graph/overview）返回统一的 GraphView 形状；
- 类型表惰性种入（MERGE on name+is_system）；
- WIKI 双链只存单向边（a→b），查询用无向匹配，画布不再出现正反两条重复边；
- 实体-来源关联落为 (Note|Doc)-[:MENTIONS {mention_count, context_json}]->(Entity) 边，
  context 以 JSON 字符串存储（Neo4j 列表属性只能是标量）；
- Chunk 级 MENTIONS 与来源级 MENTIONS 同名不同端点（Chunk→Entity）。

驱动由 neo4j_client.get_neo4j_driver() 提供，store 自身无状态、可安全并发复用。
"""
import json
import re
import uuid

from app.graph.schemas.graph import (
    ChunkHit,
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
from app.graph.storage.neo4j_client import get_neo4j_driver

_SYSTEM_TYPES = (("person", "人物"), ("tech", "技术/工具"), ("concept", "概念"),
                 ("org", "组织"), ("place", "地点"), ("project", "项目"), ("event", "事件"))

SEED_TYPE_COLORS = {"person": "#E4572E", "tech": "#1F6C9F", "concept": "#2A9D8F",
                    "org": "#E9C46A", "place": "#9B5DE5", "project": "#F4A261", "event": "#D90429"}

# Lucene 查询语法保留字符，全文检索前剔除，避免被当作语法解析报错
_LUCENE_SPECIALS = re.compile(r'[+\-!(){}\[\]^"~*?:\\/]')


def _to_entity(node) -> Entity:
    """Neo4j 节点属性 → Entity 模型（缺省字段回落，与 pydantic 默认值对齐）。"""
    return Entity(
        id=node["id"], user_id=node["user_id"], name=node["name"],
        display_name=node.get("display_name") or node["name"],
        type_id=node.get("type_id"), description=node.get("description"),
        aliases=list(node.get("aliases") or []), confidence=node.get("confidence") or 0.0,
        source_note_ids=list(node.get("source_note_ids") or []),
    )


def _to_type(node) -> EntityType:
    """Neo4j 节点属性 → EntityType 模型（color 缺省灰色）。"""
    return EntityType(
        id=node["id"], user_id=node.get("user_id"), name=node["name"],
        display_name=node.get("display_name") or node["name"],
        color=node.get("color") or "#888888", icon=node.get("icon"),
        is_system=bool(node.get("is_system")),
    )


def _union(a: list | None, b: list | None) -> list:
    """列表去重并集（保序），容忍 None——重复抽取的 aliases/source_note_ids 合并语义。"""
    return list(dict.fromkeys(list(a or []) + list(b or [])))


class Neo4jGraphStore(GraphStore):
    """无状态实现；session 参数仅为兼容工厂签名（get_graph_store(session)），不使用。"""

    def __init__(self, session=None):
        self.session = session

    # ---- 执行辅助 ----
    async def _run(self, query: str, params: dict | None = None) -> list:
        """执行单条查询并取回全部 records（连接池、重试由驱动托管）。"""
        driver = get_neo4j_driver()
        result = await driver.execute_query(query, params or {})
        return list(result.records)

    async def _tx(self, *steps: tuple[str, dict]) -> None:
        """单事务执行多条写语句（保证清理/重定向类多步操作的原子性）。"""
        driver = get_neo4j_driver()

        async def work(tx):
            for query, params in steps:
                res = await tx.run(query, params or {})
                await res.consume()

        async with driver.session() as session:
            await session.execute_write(work)

    # ---- 实体 ----
    async def upsert_entity(self, user_id: str, entity: EntityIn) -> Entity:
        """按名称去重 upsert：精确名命中或别名命中则并入（列表并集、confidence 取 max），否则新建。"""
        rows = await self._run(
            "MATCH (e:Entity) WHERE e.user_id=$uid AND e.name=$name RETURN e LIMIT 1",
            {"uid": user_id, "name": entity.name},
        )
        if not rows:
            # 别名命中：已有实体 aliases 包含本名 → 并入
            rows = await self._run(
                "MATCH (e:Entity) WHERE e.user_id=$uid AND $name IN coalesce(e.aliases, []) RETURN e LIMIT 1",
                {"uid": user_id, "name": entity.name},
            )
        if rows:
            node = rows[0]["e"]
            merged = {
                "id": node["id"],
                "user_id": user_id,
                "name": node["name"],
                "display_name": entity.display_name or node.get("display_name") or node["name"],
                "type_id": entity.type_id or node.get("type_id"),
                "description": entity.description or node.get("description"),
                "aliases": _union(node.get("aliases"), entity.aliases),
                "confidence": max(node.get("confidence") or 0.0, entity.confidence),
                "source_note_ids": _union(node.get("source_note_ids"), entity.source_note_ids),
            }
            await self._run(
                "MATCH (e:Entity {id: $id}) SET e.display_name=$display_name, e.type_id=$type_id, "
                "e.description=$description, e.aliases=$aliases, e.confidence=$confidence, "
                "e.source_note_ids=$source_note_ids",
                merged,
            )
            return Entity(**merged)

        created = {
            "id": str(uuid.uuid4()), "user_id": user_id, "name": entity.name,
            "display_name": entity.display_name or entity.name, "type_id": entity.type_id,
            "description": entity.description, "aliases": entity.aliases,
            "confidence": entity.confidence, "source_note_ids": entity.source_note_ids,
        }
        await self._run(
            "CREATE (e:Entity {id: $id, user_id: $user_id, name: $name, display_name: $display_name, "
            "aliases: $aliases, confidence: $confidence, source_note_ids: $source_note_ids}) "
            "SET e.type_id=$type_id, e.description=$description",
            created,
        )
        return Entity(**created)

    async def update_entity(self, user_id: str, entity_id: str, entity: EntityIn) -> Entity | None:
        """按 id 定位整体更新（支持改名），与 upsert 的按名去重语义无关；实体不存在返回 None。

        目标名与其它实体冲突抛 ValueError（(user_id, name) 唯一约束的前置友好校验）。
        """
        rows = await self._run(
            "MATCH (e:Entity) WHERE e.id=$eid AND e.user_id=$uid RETURN e LIMIT 1",
            {"eid": entity_id, "uid": user_id},
        )
        if not rows:
            return None
        conflict = await self._run(
            "MATCH (e:Entity) WHERE e.user_id=$uid AND e.name=$name AND e.id<>$eid RETURN e LIMIT 1",
            {"uid": user_id, "name": entity.name, "eid": entity_id},
        )
        if conflict:
            raise ValueError(f"实体名 {entity.name} 已被其它实体占用")
        await self._run(
            "MATCH (e:Entity) WHERE e.id=$eid AND e.user_id=$uid "
            "SET e.name=$name, e.display_name=$display_name, e.type_id=$type_id, "
            "e.description=$description, e.aliases=$aliases, e.confidence=$confidence, "
            "e.source_note_ids=$source_note_ids",
            {"eid": entity_id, "uid": user_id, "name": entity.name,
             "display_name": entity.display_name or entity.name, "type_id": entity.type_id,
             "description": entity.description, "aliases": entity.aliases,
             "confidence": entity.confidence, "source_note_ids": entity.source_note_ids},
        )
        return await self.get_entity(user_id, entity_id)

    async def get_entity(self, user_id: str, entity_id: str) -> Entity | None:
        """按 id 取单个实体；不存在返回 None。"""
        rows = await self._run(
            "MATCH (e:Entity) WHERE e.id=$eid AND e.user_id=$uid RETURN e LIMIT 1",
            {"eid": entity_id, "uid": user_id},
        )
        return _to_entity(rows[0]["e"]) if rows else None

    async def search_entities(self, user_id: str, query: str, limit: int) -> list[Entity]:
        """名称/展示名大小写无关包含匹配（图谱检索候选词匹配、搜索框联想共用）。"""
        rows = await self._run(
            "MATCH (e:Entity) WHERE e.user_id=$uid AND "
            "(toLower(e.name) CONTAINS toLower($q) OR toLower(e.display_name) CONTAINS toLower($q)) "
            "RETURN e LIMIT $limit",
            {"uid": user_id, "q": query or "", "limit": limit},
        )
        return [_to_entity(r["e"]) for r in rows]

    async def delete_entity(self, user_id: str, entity_id: str) -> None:
        # DETACH DELETE 连带删除该实体的全部 RELATES_TO 与两级 MENTIONS 边
        await self._run(
            "MATCH (e:Entity) WHERE e.id=$eid AND e.user_id=$uid DETACH DELETE e",
            {"eid": entity_id, "uid": user_id},
        )

    async def merge_entities(self, user_id: str, target_id: str, source_id: str) -> Entity:
        """合并实体：source 的关系与两级 MENTIONS 重定向到 target 后删除 source（单事务），属性并合。"""
        rows = await self._run(
            "MATCH (t:Entity) WHERE t.id=$tid AND t.user_id=$uid "
            "OPTIONAL MATCH (s:Entity) WHERE s.id=$sid AND s.user_id=$uid "
            "RETURN t, s",
            {"tid": target_id, "sid": source_id, "uid": user_id},
        )
        if not rows or rows[0]["s"] is None:
            raise ValueError("合并目标或源实体不存在")
        target_node = rows[0]["t"]
        source_node = rows[0]["s"]

        await self._tx(
            # 出边重定向：source→other 变为 target→other（新边新 id，避免与旧边 id 冲突）
            ("MATCH (s:Entity {id: $sid})-[r:RELATES_TO]->(o:Entity) "
             "MATCH (t:Entity {id: $tid}) "
             "CREATE (t)-[r2:RELATES_TO]->(o) SET r2 = properties(r), r2.id = randomUUID(), r2.source_id = $tid",
             {"sid": source_id, "tid": target_id}),
            # 入边重定向
            ("MATCH (o:Entity)-[r:RELATES_TO]->(s:Entity {id: $sid}) "
             "MATCH (t:Entity {id: $tid}) "
             "CREATE (o)-[r2:RELATES_TO]->(t) SET r2 = properties(r), r2.id = randomUUID(), r2.target_id = $tid",
             {"sid": source_id, "tid": target_id}),
            # 来源级 MENTIONS 重定向
            ("MATCH (src)-[m:MENTIONS]->(s:Entity {id: $sid}) "
             "MATCH (t:Entity {id: $tid}) "
             "CREATE (src)-[m2:MENTIONS]->(t) SET m2 = properties(m), m2.id = randomUUID()",
             {"sid": source_id, "tid": target_id}),
            # Chunk 级 MENTIONS 重定向
            ("MATCH (c:Chunk)-[m:MENTIONS]->(s:Entity {id: $sid}) "
             "MATCH (t:Entity {id: $tid}) "
             "CREATE (c)-[:MENTIONS]->(t)",
             {"sid": source_id, "tid": target_id}),
            ("MATCH (s:Entity {id: $sid}) DETACH DELETE s", {"sid": source_id}),
        )

        merged_aliases = _union(target_node.get("aliases"), source_node.get("aliases"))
        merged_confidence = max(target_node.get("confidence") or 0.0, source_node.get("confidence") or 0.0)
        merged_description = target_node.get("description") or source_node.get("description")
        await self._run(
            "MATCH (t:Entity {id: $tid}) SET t.aliases=$aliases, t.confidence=$confidence, "
            "t.description=$description",
            {"tid": target_id, "aliases": merged_aliases,
             "confidence": merged_confidence, "description": merged_description},
        )
        return await self.get_entity(user_id, target_id)

    # ---- 关系 ----
    async def create_relation(self, user_id: str, rel: RelationIn) -> Relation:
        """在两实体间新建 RELATES_TO 边（properties 序列化为 JSON 存储）。

        任一端实体不存在抛 ValueError（防幽灵成功：MATCH 不中时 CREATE 静默不执行）。
        """
        rows = await self._run(
            "MATCH (a:Entity {id: $source_id, user_id: $uid}), (b:Entity {id: $target_id, user_id: $uid}) "
            "RETURN a.id AS a, b.id AS b LIMIT 1",
            {"source_id": rel.source_id, "target_id": rel.target_id, "uid": user_id},
        )
        if not rows:
            raise ValueError("源或目标实体不存在，无法创建关系")
        rid = str(uuid.uuid4())
        await self._run(
            "MATCH (a:Entity {id: $source_id, user_id: $uid}), (b:Entity {id: $target_id, user_id: $uid}) "
            "CREATE (a)-[r:RELATES_TO {id: $rid, user_id: $uid, relation_type: $relation_type, "
            "confidence: $confidence, source_id: $source_id, target_id: $target_id, "
            "properties_json: $properties_json}]->(b)",
            {"source_id": rel.source_id, "target_id": rel.target_id, "uid": user_id, "rid": rid,
             "relation_type": rel.relation_type, "confidence": rel.confidence,
             "properties_json": json.dumps(rel.properties or {}, ensure_ascii=False)},
        )
        return Relation(id=rid, user_id=user_id, source_id=rel.source_id, target_id=rel.target_id,
                        relation_type=rel.relation_type, properties=rel.properties or {},
                        confidence=rel.confidence)

    async def delete_relation(self, user_id: str, relation_id: str) -> None:
        await self._run(
            "MATCH ()-[r:RELATES_TO {id: $rid}]->() WHERE r.user_id=$uid DELETE r",
            {"rid": relation_id, "uid": user_id},
        )

    # ---- 查询 ----
    async def get_neighbors(self, user_id: str, entity_id: str, depth: int) -> GraphView:
        """RELATES_TO 逐层 BFS 取邻居子图（depth 钳制 1~3），实体扩散探索用。"""
        depth = max(1, min(depth, 3))
        ids = {entity_id}
        frontier = {entity_id}
        edges: dict[str, dict] = {}
        for _ in range(depth):
            rows = await self._run(
                "MATCH (e:Entity)-[r:RELATES_TO]-(n:Entity) "
                "WHERE e.user_id=$uid AND e.id IN $ids AND n.user_id=$uid "
                "RETURN r.id AS id, r.relation_type AS relation_type, "
                "startNode(r).id AS source, endNode(r).id AS target, n.id AS nid",
                {"uid": user_id, "ids": list(frontier)},
            )
            nxt = set()
            for row in rows:
                edges[row["id"]] = row
                if row["nid"] not in ids:
                    nxt.add(row["nid"])
            frontier = nxt
            ids |= nxt
        entity_rows = await self._run(
            "MATCH (e:Entity) WHERE e.user_id=$uid AND e.id IN $ids RETURN e",
            {"uid": user_id, "ids": list(ids)},
        )
        nodes = [GraphNode(id=n["e"]["id"], label=n["e"].get("display_name") or n["e"]["name"],
                           node_type="entity", entity_type_id=n["e"].get("type_id")) for n in entity_rows]
        return GraphView(
            nodes=nodes,
            edges=[GraphEdge(id=e["id"], source=e["source"], target=e["target"],
                             kind="relation", relation_type=e["relation_type"]) for e in edges.values()],
        )

    async def get_note_graph(self, user_id: str, note_id: str) -> GraphView:
        """笔记子图：双链相邻笔记 + 它们 MENTIONS 的实体 + 实体间 RELATES_TO。"""
        # 双链边（无向匹配）+ 笔记节点集合
        wiki_rows = await self._run(
            "MATCH (n1:Note)-[w:WIKI]-(n2:Note) "
            "WHERE n1.user_id=$uid AND n2.user_id=$uid AND (n1.id=$nid OR n2.id=$nid) "
            "RETURN w.id AS id, w.kind AS kind, startNode(w).id AS source, endNode(w).id AS target, "
            "n1.id AS n1id, n2.id AS n2id, n1.title AS n1title, n2.title AS n2title",
            {"nid": note_id, "uid": user_id},
        )
        note_ids = {note_id}
        note_titles: dict[str, str] = {}
        for row in wiki_rows:
            note_ids.update((row["n1id"], row["n2id"]))
            note_titles.update({row["n1id"]: row["n1title"], row["n2id"]: row["n2title"]})

        # 相关联的笔记节点 → 来源级 MENTIONS → 实体
        mention_rows = await self._run(
            "MATCH (src:Note)-[m:MENTIONS]->(e:Entity) "
            "WHERE src.user_id=$uid AND src.id IN $note_ids AND e.user_id=$uid "
            "RETURN m.id AS id, src.id AS source, e.id AS target, e.name AS ename, "
            "e.display_name AS edisplay, e.type_id AS etype",
            {"note_ids": list(note_ids), "uid": user_id},
        )
        entity_ids = list({row["target"] for row in mention_rows})
        rel_rows = []
        if entity_ids:
            rel_rows = await self._run(
                "MATCH (a:Entity)-[r:RELATES_TO]-(b:Entity) "
                "WHERE a.user_id=$uid AND b.user_id=$uid AND (a.id IN $ids OR b.id IN $ids) "
                "RETURN r.id AS id, r.relation_type AS relation_type, "
                "startNode(r).id AS source, endNode(r).id AS target",
                {"ids": entity_ids, "uid": user_id},
            )

        nodes = [GraphNode(id=nid, label=note_titles.get(nid) or "笔记", node_type="note")
                 for nid in note_ids]
        nodes += [GraphNode(id=row["target"], label=row["edisplay"] or row["ename"],
                            node_type="entity", entity_type_id=row["etype"]) for row in mention_rows]
        # 按 id 去重实体节点（同一实体被多条提及边带出）
        seen: set[str] = set()
        nodes = [n for n in nodes if not (n.id in seen or seen.add(n.id))]
        edges = [GraphEdge(id=row["id"], source=row["source"], target=row["target"],
                           kind=row["kind"]) for row in wiki_rows]
        edges += [GraphEdge(id=row["id"], source=row["source"], target=row["target"],
                            kind="relation", relation_type="提及") for row in mention_rows]
        edges += [GraphEdge(id=row["id"], source=row["source"], target=row["target"],
                            kind="relation", relation_type=row["relation_type"]) for row in rel_rows]
        return GraphView(nodes=nodes, edges=edges)

    async def get_entity_notes(self, user_id: str, entity_id: str) -> list[EntityNoteLink]:
        """反查实体的来源关联（Note/Doc → MENTIONS），context JSON 还原为 list[dict]。"""
        rows = await self._run(
            "MATCH (src)-[m:MENTIONS]->(e:Entity {id: $eid}) "
            "WHERE e.user_id=$uid AND src.user_id=$uid AND (src:Note OR src:Doc) "
            "RETURN src.id AS source_id, labels(src) AS labels, "
            "src.title AS title, src.filename AS filename, "
            "m.mention_count AS mention_count, m.context_json AS context_json",
            {"eid": entity_id, "uid": user_id},
        )
        links = []
        for row in rows:
            is_doc = "Doc" in (row["labels"] or [])
            context = []
            try:
                context = json.loads(row["context_json"] or "[]")
            except (TypeError, ValueError):
                pass
            links.append(EntityNoteLink(
                entity_id=entity_id, note_id=row["source_id"],
                source_type="doc" if is_doc else "note",
                source_name=row["filename"] if is_doc else row["title"],
                mention_count=row["mention_count"] or 0, context=context))
        return links

    async def get_overview(self, user_id: str, type_ids: list[str] | None, limit: int) -> GraphView:
        """总览子图：实体（可按类型过滤/限量）+ 实体关系 + 笔记/文档提及边 + WIKI 双链，画布首页用。"""
        entity_filter = "AND e.type_id IN $type_ids" if type_ids else ""
        entity_rows = await self._run(
            f"MATCH (e:Entity) WHERE e.user_id=$uid {entity_filter} RETURN e LIMIT $limit",
            {"uid": user_id, "type_ids": type_ids or [], "limit": limit},
        )
        entities = [r["e"] for r in entity_rows]
        eids = [e["id"] for e in entities]

        rel_rows, note_mention_rows, wiki_rows, docs, doc_mention_rows = [], [], [], [], []
        if eids:
            rel_rows = await self._run(
                "MATCH (a:Entity)-[r:RELATES_TO]-(b:Entity) "
                "WHERE a.user_id=$uid AND b.user_id=$uid AND (a.id IN $ids OR b.id IN $ids) "
                "RETURN r.id AS id, r.relation_type AS relation_type, "
                "startNode(r).id AS source, endNode(r).id AS target",
                {"ids": eids, "uid": user_id},
            )
            note_mention_rows = await self._run(
                "MATCH (src:Note)-[m:MENTIONS]->(e:Entity) "
                "WHERE e.user_id=$uid AND e.id IN $ids AND src.user_id=$uid "
                "RETURN m.id AS id, src.id AS source, src.title AS title, e.id AS target, "
                "e.display_name AS edisplay, e.type_id AS etype",
                {"ids": eids, "uid": user_id},
            )
            doc_mention_rows = await self._run(
                "MATCH (src:Doc)-[m:MENTIONS]->(e:Entity) "
                "WHERE e.user_id=$uid AND e.id IN $ids AND src.user_id=$uid "
                "RETURN m.id AS id, src.id AS source, src.filename AS filename, e.id AS target, "
                "e.display_name AS edisplay, e.type_id AS etype",
                {"ids": eids, "uid": user_id},
            )
        note_ids = {row["source"] for row in note_mention_rows}
        if note_ids:
            # wiki 边另一端可能是不含实体关联的笔记（仅被双链引用）——补齐节点，避免悬空边
            wiki_rows = await self._run(
                "MATCH (n1:Note)-[w:WIKI]-(n2:Note) "
                "WHERE n1.user_id=$uid AND n2.user_id=$uid AND (n1.id IN $ids OR n2.id IN $ids) "
                "RETURN w.id AS id, w.kind AS kind, startNode(w).id AS source, endNode(w).id AS target, "
                "n1.id AS n1id, n2.id AS n2id, n1.title AS n1title, n2.title AS n2title",
                {"ids": list(note_ids), "uid": user_id},
            )
            note_ids |= {row["n1id"] for row in wiki_rows} | {row["n2id"] for row in wiki_rows}
        docs = await self._run(
            "MATCH (d:Doc) WHERE d.user_id=$uid RETURN d", {"uid": user_id})

        note_titles: dict[str, str] = {}
        for row in note_mention_rows:
            note_titles[row["source"]] = row["title"]
        for row in wiki_rows:
            note_titles.update({row["n1id"]: row["n1title"], row["n2id"]: row["n2title"]})

        nodes = [GraphNode(id=e["id"], label=e.get("display_name") or e["name"],
                           node_type="entity", entity_type_id=e.get("type_id")) for e in entities]
        nodes += [GraphNode(id=nid, label=note_titles.get(nid) or "笔记", node_type="note")
                  for nid in note_ids]
        nodes += [GraphNode(id=d["id"], label=d.get("filename") or "文档", node_type="doc")
                  for d in (r["d"] for r in docs)]
        edges = [GraphEdge(id=r["id"], source=r["source"], target=r["target"],
                           kind="relation", relation_type=r["relation_type"]) for r in rel_rows]
        edges += [GraphEdge(id=m["id"], source=m["source"], target=m["target"],
                            kind="relation", relation_type="提及")
                  for m in note_mention_rows + doc_mention_rows]
        edges += [GraphEdge(id=w["id"], source=w["source"], target=w["target"], kind=w["kind"])
                  for w in wiki_rows]
        return GraphView(nodes=nodes, edges=edges)

    async def get_doc_graph(self, user_id: str, doc_id: str) -> GraphView:
        """文档子图：文档节点 + 其 MENTIONS 实体 + 实体间 RELATES_TO。"""
        doc_rows = await self._run(
            "MATCH (d:Doc) WHERE d.id=$did AND d.user_id=$uid RETURN d LIMIT 1",
            {"did": doc_id, "uid": user_id},
        )
        mention_rows = await self._run(
            "MATCH (src:Doc)-[m:MENTIONS]->(e:Entity) "
            "WHERE src.id=$did AND src.user_id=$uid AND e.user_id=$uid "
            "RETURN m.id AS id, src.id AS source, e.id AS target, e.name AS ename, "
            "e.display_name AS edisplay, e.type_id AS etype",
            {"did": doc_id, "uid": user_id},
        )
        entity_ids = list({row["target"] for row in mention_rows})
        rel_rows = []
        if entity_ids:
            rel_rows = await self._run(
                "MATCH (a:Entity)-[r:RELATES_TO]-(b:Entity) "
                "WHERE a.user_id=$uid AND b.user_id=$uid AND (a.id IN $ids OR b.id IN $ids) "
                "RETURN r.id AS id, r.relation_type AS relation_type, "
                "startNode(r).id AS source, endNode(r).id AS target",
                {"ids": entity_ids, "uid": user_id},
            )
        filename = doc_rows[0]["d"].get("filename") if doc_rows else None
        nodes = [GraphNode(id=doc_id, label=filename or "文档", node_type="doc")]
        nodes += [GraphNode(id=row["target"], label=row["edisplay"] or row["ename"],
                            node_type="entity", entity_type_id=row["etype"]) for row in mention_rows]
        edges = [GraphEdge(id=row["id"], source=row["source"], target=row["target"],
                           kind="relation", relation_type="提及") for row in mention_rows]
        edges += [GraphEdge(id=row["id"], source=row["source"], target=row["target"],
                            kind="relation", relation_type=row["relation_type"]) for row in rel_rows]
        return GraphView(nodes=nodes, edges=edges)

    # ---- 类型 ----
    async def list_types(self, user_id: str) -> list[EntityType]:
        """用户可见类型列表；系统预置类型在此惰性种入（首次调用自动补齐）。"""
        # 惰性种入系统预置类型（MERGE 键含 is_system，不会误并用户同名类型）
        await self._run(
            "UNWIND $rows AS row MERGE (t:EntityType {name: row.name, is_system: true}) "
            "ON CREATE SET t.id = randomUUID(), t.display_name = row.display_name, t.color = row.color",
            {"rows": [{"name": name, "display_name": disp, "color": SEED_TYPE_COLORS[name]}
                      for name, disp in _SYSTEM_TYPES]},
        )
        rows = await self._run(
            "MATCH (t:EntityType) WHERE t.user_id IS NULL OR t.user_id=$uid RETURN t",
            {"uid": user_id},
        )
        return [_to_type(r["t"]) for r in rows]

    async def upsert_type(self, user_id: str, type_in: TypeIn) -> EntityType:
        """按 (user_id, name) MERGE 类型：已存在则更新展示属性，不存在则新建。"""
        rows = await self._run(
            "MERGE (t:EntityType {name: $name, user_id: $uid}) "
            "ON CREATE SET t.id = randomUUID(), t.is_system = false, t.color = $color, t.icon = $icon, "
            "t.display_name = $display_name "
            "ON MATCH SET t.display_name = $display_name, t.color = $color, t.icon = $icon "
            "RETURN t",
            {"name": type_in.name, "uid": user_id, "display_name": type_in.display_name,
             "color": type_in.color, "icon": type_in.icon},
        )
        return _to_type(rows[0]["t"])

    async def delete_type(self, user_id: str, type_id: str) -> None:
        # 实体 type_id 置空降级未分类（SET NULL 即删除属性），不级联删实体
        await self._tx(
            ("MATCH (e:Entity) WHERE e.user_id=$uid AND e.type_id=$tid SET e.type_id = NULL",
             {"uid": user_id, "tid": type_id}),
            ("MATCH (t:EntityType) WHERE t.id=$tid AND t.user_id=$uid DETACH DELETE t",
             {"uid": user_id, "tid": type_id}),
        )

    # ---- Chunk 检索扩展 ----
    async def source_entity_candidates(self, user_id: str, source_type: str, source_id: str) -> list[str]:
        """某来源此前关联的实体 id（source_note_ids 成员）——重抽后收缩引用的候选集。"""
        rows = await self._run(
            "MATCH (e:Entity) WHERE e.user_id=$uid AND $sid IN coalesce(e.source_note_ids, []) "
            "RETURN collect(DISTINCT e.id) AS ids",
            {"uid": user_id, "sid": source_id},
        )
        return list(rows[0]["ids"] or []) if rows else []

    async def ensure_source_node(self, user_id: str, source_type: str, source_id: str, title: str) -> None:
        """确保 Note/Doc 源节点存在（作为 MENTIONS 边端点），缺失时按标题补建。

        MERGE 键含 user_id：文档 source_id 是内容 md5，跨用户可重复，必须按用户隔离。
        """
        if source_type == "doc":
            await self._run(
                "MERGE (d:Doc {id: $sid, user_id: $uid}) "
                "ON CREATE SET d.created_at = timestamp() "
                "SET d.filename = $title",
                {"sid": source_id, "uid": user_id, "title": title},
            )
        else:
            await self._run(
                "MERGE (n:Note {id: $sid, user_id: $uid}) "
                "SET n.title = $title",
                {"sid": source_id, "uid": user_id, "title": title},
            )

    async def upsert_chunks(self, user_id: str, source_type: str, source_id: str, source_name: str,
                            chunks: list[dict]) -> None:
        """批量写入来源 Chunk；业务键为 (user_id, 来源类型:来源ID:序号)，MERGE 保证重复抽取幂等覆盖。

        文档 source_id 是 md5 跨用户可重复，id 不含 user_id 时会互相覆盖——隔离全靠复合 MERGE 键。
        """
        rows = []
        for chunk in chunks:
            rows.append({
                "id": f"{source_type}:{source_id}:{chunk['chunk_index']}",
                "user_id": user_id, "kind": source_type, "source_id": source_id,
                "source_name": source_name, "chunk_index": chunk["chunk_index"],
                "text": chunk["text"], "embedding": chunk.get("embedding"),
                "page": chunk.get("page"), "image_paths": chunk.get("image_paths"),
            })
        if not rows:
            return
        await self._run(
            "UNWIND $rows AS row "
            "MERGE (c:Chunk {id: row.id, user_id: row.user_id}) "
            "SET c.kind = row.kind, c.source_id = row.source_id, "
            "c.source_name = row.source_name, c.chunk_index = row.chunk_index, c.text = row.text, "
            "c.embedding = row.embedding, c.page = row.page, c.image_paths = row.image_paths",
            {"rows": rows},
        )

    async def delete_chunks_by_source(self, user_id: str, source_type: str, source_id: str) -> None:
        await self._run(
            "MATCH (c:Chunk) WHERE c.user_id=$uid AND c.kind=$kind AND c.source_id=$sid DETACH DELETE c",
            {"uid": user_id, "kind": source_type, "sid": source_id},
        )

    async def set_source_mentions(self, user_id: str, source_type: str, source_id: str,
                                  links: list[dict]) -> None:
        """links: [{entity_id, mention_count, context(list[dict])}]；先清后插放同一事务（防中途失败半无关联）。

        context 序列化为 JSON（Neo4j 列表属性只能是标量）。
        """
        rows = [{"entity_id": link["entity_id"], "mention_count": link.get("mention_count", 0),
                 "context_json": json.dumps(link.get("context") or [], ensure_ascii=False)}
                for link in links]
        await self._tx(
            ("MATCH (src) WHERE (src:Note OR src:Doc) AND src.id=$sid AND src.user_id=$uid "
             "MATCH (src)-[m:MENTIONS]->() DELETE m",
             {"sid": source_id, "uid": user_id}),
            ("UNWIND $rows AS row "
             "MATCH (src) WHERE (src:Note OR src:Doc) AND src.id=$sid AND src.user_id=$uid "
             "MATCH (e:Entity {id: row.entity_id, user_id: $uid}) "
             "CREATE (src)-[:MENTIONS {id: randomUUID(), mention_count: row.mention_count, "
             "context_json: row.context_json}]->(e)",
             {"rows": rows, "sid": source_id, "uid": user_id}),
        )

    async def set_chunk_mentions(self, user_id: str, source_type: str, source_id: str,
                                 links: list[dict]) -> None:
        """links: [{entity_id, chunk_indexes}]；先清后插放同一事务（防中途失败半无关联）。"""
        rows = [{"entity_id": link["entity_id"],
                 "chunk_ids": [f"{source_type}:{source_id}:{idx}" for idx in link.get("chunk_indexes", [])]}
                for link in links]
        rows = [row for row in rows if row["chunk_ids"]]
        await self._tx(
            ("MATCH (:Chunk {user_id: $uid, kind: $kind, source_id: $sid})-[m:MENTIONS]->() DELETE m",
             {"uid": user_id, "kind": source_type, "sid": source_id}),
            ("UNWIND $rows AS row "
             "MATCH (c:Chunk) WHERE c.id IN row.chunk_ids AND c.user_id = $uid "
             "MATCH (e:Entity {id: row.entity_id, user_id: $uid}) "
             "CREATE (c)-[:MENTIONS]->(e)",
             {"rows": rows, "uid": user_id}),
        )

    async def set_relations_from_source(self, user_id: str, source_type: str, source_id: str,
                                        rels: list[dict]) -> None:
        """按来源溯源替换 RELATES_TO：先删该来源此前建的边再插入本轮关系（重抽幂等，手动关系不受影响）。"""
        await self._run(
            "MATCH ()-[r:RELATES_TO]->() "
            "WHERE r.user_id=$uid AND r.source_id=$sid AND r.source_type=$stype DELETE r",
            {"uid": user_id, "sid": source_id, "stype": source_type},
        )
        if not rels:
            return
        rows = [{"source_id": rel["source_id"], "target_id": rel["target_id"],
                 "relation_type": rel["relation_type"], "confidence": rel.get("confidence", 0.7)}
                for rel in rels]
        await self._run(
            "UNWIND $rows AS row "
            "MATCH (a:Entity {id: row.source_id, user_id: $uid}), (b:Entity {id: row.target_id, user_id: $uid}) "
            "CREATE (a)-[:RELATES_TO {id: randomUUID(), user_id: $uid, relation_type: row.relation_type, "
            "confidence: row.confidence, source_id: $sid, source_type: $stype}]->(b)",
            {"rows": rows, "uid": user_id, "sid": source_id, "stype": source_type},
        )

    async def set_note_wiki_edges(self, user_id: str, note_id: str, links: list[dict]) -> None:
        """先清本笔记全部出边再插入（单向存储）。

        目标笔记的 Neo4j 节点可能尚未被其自身抽取建出（回填并发乱序/先链接后创建），
        故按链接标题就地 MERGE 补建目标节点，不依赖处理顺序。
        links 每项：{target_note_id, target_title?, kind?}——target_title 须已通过
        MySQL 笔记标题校验（双链只指向真实存在的笔记）。
        """
        await self._run(
            "MATCH (:Note {id: $nid, user_id: $uid})-[w:WIKI]->() DELETE w",
            {"nid": note_id, "uid": user_id},
        )
        if not links:
            return
        rows = [{"target_note_id": link["target_note_id"],
                 "target_title": link.get("target_title") or "",
                 "kind": link.get("kind", "wiki")}
                for link in links]
        await self._run(
            "UNWIND $rows AS row "
            "MATCH (n:Note {id: $nid, user_id: $uid}) "
            "MERGE (t:Note {id: row.target_note_id}) "
            "ON CREATE SET t.user_id = $uid, t.title = row.target_title "
            "CREATE (n)-[:WIKI {id: randomUUID(), source_note_id: $nid, "
            "target_note_id: row.target_note_id, kind: row.kind}]->(t)",
            {"rows": rows, "nid": note_id, "uid": user_id},
        )

    async def search_chunks(self, user_id: str, query_embedding: list[float] | None,
                            text_query: str | None, kinds: list[str] | None, limit: int) -> list[ChunkHit]:
        """Chunk 混合检索：向量 + 全文两路各取 2×limit 候选，RRF（k=60）融合排序取 top limit。

        embedding 为 None（query 向量化失败）时退化为纯全文检索；score 返回 RRF 融合分，
        原始两路分数放在 metadata 供前端展示。
        """
        limit = max(1, limit)
        pool = limit * 2
        kind_filter = "AND (size($kinds) = 0 OR node.kind IN $kinds)"
        rrf: dict[str, float] = {}
        raw_scores: dict[str, dict] = {}

        if query_embedding:
            rows = await self._run(
                "CALL db.index.vector.queryNodes('chunk_embedding_index', $k, $embedding) "
                f"YIELD node, score WHERE node.user_id = $uid {kind_filter} "
                "RETURN node.id AS id, score",
                {"k": pool, "embedding": query_embedding, "uid": user_id, "kinds": kinds or []},
            )
            for rank, row in enumerate(rows, start=1):
                rrf[row["id"]] = rrf.get(row["id"], 0.0) + 1.0 / (60 + rank)
                raw_scores.setdefault(row["id"], {})["vector_score"] = row["score"]

        if text_query:
            cleaned = _LUCENE_SPECIALS.sub(" ", text_query).strip()
            if cleaned:
                rows = await self._run(
                    "CALL db.index.fulltext.queryNodes('chunk_text_index', $q) "
                    f"YIELD node, score WHERE node.user_id = $uid {kind_filter} "
                    "RETURN node.id AS id, score LIMIT $k",
                    {"q": cleaned, "uid": user_id, "kinds": kinds or [], "k": pool},
                )
                for rank, row in enumerate(rows, start=1):
                    rrf[row["id"]] = rrf.get(row["id"], 0.0) + 1.0 / (60 + rank)
                    raw_scores.setdefault(row["id"], {})["fulltext_score"] = row["score"]

        if not rrf:
            return []
        top_ids = [cid for cid, _ in sorted(rrf.items(), key=lambda kv: kv[1], reverse=True)[:limit]]
        # id 跨用户可重复（文档 md5 相同），取节点必须叠加 user_id 过滤防串用户
        node_rows = await self._run(
            "MATCH (c:Chunk) WHERE c.id IN $ids AND c.user_id = $uid RETURN c", {"ids": top_ids, "uid": user_id})
        by_id = {row["c"]["id"]: row["c"] for row in node_rows}
        hits = []
        for cid in top_ids:
            node = by_id.get(cid)
            if node is None:
                continue
            hits.append(ChunkHit(
                id=cid, kind=node.get("kind") or "doc", source_id=node.get("source_id") or "",
                source_name=node.get("source_name"), chunk_index=node.get("chunk_index") or 0,
                text=node.get("text") or "", score=rrf[cid], metadata=raw_scores.get(cid, {})))
        return hits

    async def get_chunks_mentioning(self, user_id: str, entity_ids: list[str], limit: int) -> list[ChunkHit]:
        """取提及指定实体的 Chunk 原文（图谱证据补正文可引用；score 为 None 不参与排序）。

        Chunk 按用户过滤：文档 md5 跨用户可重复，不同用户的同 id Chunk 是不同节点。
        """
        if not entity_ids:
            return []
        rows = await self._run(
            "MATCH (c:Chunk)-[:MENTIONS]->(e:Entity) "
            "WHERE e.user_id=$uid AND e.id IN $ids AND c.user_id=$uid RETURN c LIMIT $limit",
            {"uid": user_id, "ids": entity_ids, "limit": max(1, limit)},
        )
        return [ChunkHit(
            id=row["c"]["id"], kind=row["c"].get("kind") or "doc",
            source_id=row["c"].get("source_id") or "", source_name=row["c"].get("source_name"),
            chunk_index=row["c"].get("chunk_index") or 0, text=row["c"].get("text") or "",
            score=None, metadata={"via": "mentions"}) for row in rows]

    async def clear_source_data(self, user_id: str, source_type: str, source_id: str) -> list[str]:
        """删除源节点（连带来源级 MENTIONS 与 WIKI 边）、Chunk 及溯源关系；返回曾关联的实体 id。"""
        cand_rows = await self._run(
            "MATCH (src)-[:MENTIONS]->(e:Entity) "
            "WHERE src.id=$sid AND src.user_id=$uid AND (src:Note OR src:Doc) "
            "RETURN DISTINCT e.id AS eid",
            {"sid": source_id, "uid": user_id},
        )
        candidates = [row["eid"] for row in cand_rows]
        await self._tx(
            ("MATCH (src) WHERE (src:Note OR src:Doc) AND src.id=$sid AND src.user_id=$uid DETACH DELETE src",
             {"sid": source_id, "uid": user_id}),
            ("MATCH (c:Chunk) WHERE c.user_id=$uid AND c.kind=$kind AND c.source_id=$sid DETACH DELETE c",
             {"uid": user_id, "kind": source_type, "sid": source_id}),
            ("MATCH ()-[r:RELATES_TO]->() WHERE r.user_id=$uid AND r.source_id=$sid DELETE r",
             {"uid": user_id, "sid": source_id}),
        )
        return candidates

    async def sweep_orphan_entities(self, user_id: str, candidate_entity_ids: list[str],
                                    removed_source_ids: list[str]) -> None:
        """孤儿清扫：摘除被删来源引用；无剩余引用且无任何 MENTIONS 边的实体级联删除。"""
        if not removed_source_ids or not candidate_entity_ids:
            return
        await self._run(
            "UNWIND $ids AS eid "
            "MATCH (e:Entity {id: eid, user_id: $uid}) "
            "SET e.source_note_ids = [x IN coalesce(e.source_note_ids, []) WHERE NOT x IN $removed] "
            "WITH e WHERE size(coalesce(e.source_note_ids, [])) = 0 "
            "AND NOT EXISTS { ()-[:MENTIONS]->(e) } "
            "DETACH DELETE e",
            {"ids": list(dict.fromkeys(candidate_entity_ids)), "removed": list(removed_source_ids),
             "uid": user_id},
        )

    async def clear_all_docs(self, user_id: str) -> None:
        """清空该用户全部文档图谱：Doc 节点、doc Chunk、doc 溯源关系，最后孤儿清扫实体。"""
        cand_rows = await self._run(
            "MATCH (d:Doc)-[:MENTIONS]->(e:Entity) WHERE d.user_id=$uid RETURN DISTINCT e.id AS eid",
            {"uid": user_id},
        )
        doc_rows = await self._run(
            "MATCH (d:Doc) WHERE d.user_id=$uid RETURN d.id AS did", {"uid": user_id})
        candidates = [row["eid"] for row in cand_rows]
        removed_doc_ids = [row["did"] for row in doc_rows]
        await self._tx(
            ("MATCH (d:Doc) WHERE d.user_id=$uid DETACH DELETE d", {"uid": user_id}),
            ("MATCH (c:Chunk) WHERE c.user_id=$uid AND c.kind='doc' DETACH DELETE c", {"uid": user_id}),
            ("MATCH ()-[r:RELATES_TO]->() WHERE r.user_id=$uid AND r.source_type='doc' DELETE r",
             {"uid": user_id}),
        )
        await self.sweep_orphan_entities(user_id, candidates, removed_doc_ids)
