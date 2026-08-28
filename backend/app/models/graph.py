"""知识图谱六张表 ORM 模型（全部带 user_id 隔离）。"""
from sqlalchemy import Boolean, Column, DateTime, Float, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.sql import func

from app.models.chat_history import Base


class GraphEntityType(Base):
    __tablename__ = "graph_entity_types"

    id = Column(String(36), primary_key=True, comment="UUID")
    user_id = Column(String(36), nullable=True, comment="NULL=系统预置，非NULL=用户自定义")
    name = Column(String(50), nullable=False, comment="类型标识，如 person/tech")
    display_name = Column(String(50), nullable=False, comment="显示名，如 人物")
    color = Column(String(20), nullable=False, comment="十六进制颜色")
    icon = Column(String(100), nullable=True, comment="图标标识")
    is_system = Column(Boolean, default=False, nullable=False, comment="是否系统预置")
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class GraphEntity(Base):
    __tablename__ = "graph_entities"
    __table_args__ = (UniqueConstraint("user_id", "name", name="uq_graph_entity_user_name"),)

    id = Column(String(36), primary_key=True, comment="UUID")
    user_id = Column(String(36), index=True, nullable=False)
    name = Column(String(200), nullable=False, comment="规范化名")
    display_name = Column(String(200), nullable=False, comment="展示名")
    type_id = Column(String(36), nullable=True, comment="关联 graph_entity_types.id，空=未分类")
    description = Column(Text, nullable=True)
    aliases = Column(JSON, default=list, comment="别名表")
    confidence = Column(Float, default=0.0, nullable=False)
    source_note_ids = Column(JSON, default=list, comment="首次来源笔记 id 列表")
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class GraphRelation(Base):
    __tablename__ = "graph_relations"

    id = Column(String(36), primary_key=True, comment="UUID")
    user_id = Column(String(36), index=True, nullable=False)
    source_id = Column(String(36), index=True, nullable=False)
    target_id = Column(String(36), index=True, nullable=False)
    relation_type = Column(String(50), nullable=False)
    properties = Column(JSON, default=dict)
    confidence = Column(Float, default=0.0, nullable=False)
    source_note_id = Column(String(36), nullable=True, comment="来源笔记 UUID 或文档 md5（NULL=手动创建，不被自动清理）")
    source_type = Column(String(10), nullable=True, comment="note/doc（NULL=手动创建，不被自动清理）")
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class GraphEntityNote(Base):
    __tablename__ = "graph_entity_notes"

    id = Column(String(36), primary_key=True, comment="UUID")
    user_id = Column(String(36), index=True, nullable=False)
    entity_id = Column(String(36), index=True, nullable=False)
    note_id = Column(String(36), index=True, nullable=False, comment="笔记 UUID 或文档 md5")
    source_type = Column(String(10), default="note", server_default="note", nullable=False,
                         comment="note/doc")
    mention_count = Column(Integer, default=0, nullable=False)
    context = Column(JSON, default=list, comment="提及证据片段列表")
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class GraphNoteEdge(Base):
    __tablename__ = "graph_note_edges"

    id = Column(String(36), primary_key=True, comment="UUID")
    user_id = Column(String(36), index=True, nullable=False)
    source_note_id = Column(String(36), index=True, nullable=False)
    target_note_id = Column(String(36), index=True, nullable=False)
    kind = Column(String(20), default="wiki", nullable=False, comment="wiki/auto")
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class GraphExtractLog(Base):
    __tablename__ = "graph_extract_logs"
    __table_args__ = (UniqueConstraint("user_id", "note_id", name="uq_graph_extract_user_note"),)

    id = Column(String(36), primary_key=True, comment="UUID")
    user_id = Column(String(36), index=True, nullable=False)
    note_id = Column(String(36), nullable=False, comment="笔记 UUID 或文档 md5")
    source_type = Column(String(10), default="note", server_default="note", nullable=False,
                         comment="note/doc")
    content_hash = Column(String(64), nullable=False)
    status = Column(String(20), default="pending", nullable=False, comment="pending/success/failed")
    new_count = Column(Integer, default=0, nullable=False)
    update_count = Column(Integer, default=0, nullable=False)
    error_message = Column(Text, nullable=True)
    triggered_at = Column(DateTime(timezone=True), server_default=func.now())
    finished_at = Column(DateTime(timezone=True), nullable=True)


class GraphDoc(Base):
    __tablename__ = "graph_docs"

    id = Column(String(36), primary_key=True, comment="文档 md5")
    user_id = Column(String(36), index=True, nullable=False)
    filename = Column(String(255), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
