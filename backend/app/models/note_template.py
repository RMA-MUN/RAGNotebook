from sqlalchemy import JSON, Boolean, Column, DateTime, Integer, String, Text
from sqlalchemy.sql import func

from app.models.chat_history import Base


class NoteTemplate(Base):
    __tablename__ = "note_templates"

    id = Column(String(36), primary_key=True, comment="UUID")
    user_id = Column(String(36), index=True, nullable=False, comment="用户ID")
    name = Column(String(100), nullable=False, comment="模板名称")
    icon = Column(String(50), default="FileText", comment="图标名称")
    category = Column(String(50), default="", comment="默认分类")
    title = Column(String(200), default="", comment="默认标题")
    content = Column(Text, default="", comment="默认内容 Markdown")
    tags = Column(JSON, default=[], comment="默认标签")
    is_default = Column(Boolean, default=False, nullable=False, comment="系统内置模板")
    sort_order = Column(Integer, default=0, comment="排序序号")
    created_at = Column(DateTime(timezone=True), server_default=func.now(), comment="创建时间")
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), comment="更新时间")
