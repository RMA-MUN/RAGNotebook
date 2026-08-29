"""知识图谱 Pydantic 模型。"""
from pydantic import BaseModel, Field


class TypeIn(BaseModel):
    name: str
    display_name: str
    color: str = "#888888"
    icon: str | None = None


class EntityType(BaseModel):
    id: str
    user_id: str | None = None
    name: str
    display_name: str
    color: str
    icon: str | None = None
    is_system: bool = False


class EntityIn(BaseModel):
    name: str
    display_name: str | None = None
    type_id: str | None = None
    description: str | None = None
    aliases: list[str] = Field(default_factory=list)
    confidence: float = 0.0
    source_note_ids: list[str] = Field(default_factory=list)


class Entity(BaseModel):
    id: str
    user_id: str
    name: str
    display_name: str
    type_id: str | None = None
    description: str | None = None
    aliases: list[str] = Field(default_factory=list)
    confidence: float = 0.0
    source_note_ids: list[str] = Field(default_factory=list)


class RelationIn(BaseModel):
    source_id: str
    target_id: str
    relation_type: str
    properties: dict = Field(default_factory=dict)
    confidence: float = 0.0


class Relation(BaseModel):
    id: str
    user_id: str
    source_id: str
    target_id: str
    relation_type: str
    properties: dict = Field(default_factory=dict)
    confidence: float = 0.0


class ExtractedEntity(BaseModel):
    name: str
    type: str | None = None
    aliases: list[str] = Field(default_factory=list)
    description: str | None = None
    mentions: list[str] = Field(default_factory=list)


class ExtractedRelation(BaseModel):
    source: str
    target: str
    relation_type: str


class ExtractResult(BaseModel):
    entities: list[ExtractedEntity]
    relations: list[ExtractedRelation] = Field(default_factory=list)


class GraphNode(BaseModel):
    id: str
    label: str
    node_type: str  # "entity" | "note" | "doc"
    entity_type_id: str | None = None


class GraphEdge(BaseModel):
    id: str
    source: str
    target: str
    kind: str  # "relation" | "wiki"
    relation_type: str | None = None


class GraphView(BaseModel):
    nodes: list[GraphNode]
    edges: list[GraphEdge]


class EntityNoteLink(BaseModel):
    entity_id: str
    note_id: str
    source_type: str = "note"  # note/doc
    source_name: str | None = None  # 文档时为其 filename
    mention_count: int = 0
    context: list[dict] = Field(default_factory=list)


class ExtractLog(BaseModel):
    note_id: str
    source_type: str = "note"
    content_hash: str
    status: str
    new_count: int = 0
    update_count: int = 0
    error_message: str | None = None


class MergeRequest(BaseModel):
    target_id: str
    source_id: str


class ChunkHit(BaseModel):
    """种子检索命中的 chunk（Neo4j Chunk 节点的检索投影）。"""
    id: str
    kind: str  # note/doc
    source_id: str  # 笔记 UUID 或文档 md5
    source_name: str | None = None  # 笔记标题或文档文件名
    chunk_index: int = 0
    text: str
    score: float | None = None  # RRF 融合分（图扩展路径可能为 None）
    metadata: dict = Field(default_factory=dict)  # vector_score/fulltext_score 等
