from sqlalchemy import Column, DateTime, ForeignKey, Integer, String
from sqlalchemy.sql import func

from app.models.chat_history import Base


class ReviewRecord(Base):
    __tablename__ = "review_records"

    id = Column(String(36), primary_key=True, comment="UUID")
    note_id = Column(String(36), ForeignKey("notes.id", ondelete="CASCADE"), nullable=False, comment="笔记ID")
    user_id = Column(String(36), index=True, nullable=False, comment="用户ID")
    last_reviewed_at = Column(DateTime(timezone=True), comment="上次回顾时间")
    review_count = Column(Integer, default=0, comment="回顾次数")
    next_review_at = Column(DateTime(timezone=True), comment="下次回顾时间")
    interval_days = Column(Integer, default=1, comment="当前间隔天数")
    created_at = Column(DateTime(timezone=True), server_default=func.now(), comment="创建时间")
