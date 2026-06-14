from sqlalchemy import JSON, Boolean, Column, DateTime, String, Text
from sqlalchemy.sql import func

from app.models.chat_history import Base


class Note(Base):
    __tablename__ = "notes"

    id = Column(String(36), primary_key=True, comment="UUID")
    user_id = Column(String(36), index=True, nullable=False, comment="用户ID")
    title = Column(String(200), nullable=False, comment="笔记标题")
    content = Column(Text, nullable=False, comment="Markdown原文")
    tags = Column(JSON, comment='标签列表 ["AI", "FastAPI"]')
    category = Column(String(50), comment="分类 work/study/life/project")
    is_pinned = Column(Boolean, default=False, nullable=False, comment="是否置顶")
    created_at = Column(DateTime(timezone=True), server_default=func.now(), comment="创建时间")
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), comment="更新时间")
