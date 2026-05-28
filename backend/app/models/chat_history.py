from sqlalchemy import JSON, Column, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import declarative_base, relationship
from sqlalchemy.sql import func

Base = declarative_base()

class ChatSession(Base):
    __tablename__ = "chat_sessions"

    id = Column(String(64), primary_key=True, index=True)
    # 通过 user_id 关联用户微服务，不做物理外键约束
    user_id = Column(String(64), index=True, nullable=False)

    title = Column(String(255), default="新的对话")
    metadata_ = Column(JSON, name="metadata")  # metadata 是 SQL 保留字，加下划线
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    # 关系
    messages = relationship("ChatMessage", back_populates="session", cascade="all, delete-orphan")

class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id = Column(Integer, primary_key=True, index=True)
    session_id = Column(String(64), ForeignKey("chat_sessions.id"))

    # LangChain 标准字段
    role = Column(String(32), nullable=False)
    content = Column(Text, nullable=False)
    metadata_ = Column(JSON, name="metadata")

    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # 关系
    session = relationship("ChatSession", back_populates="messages")
