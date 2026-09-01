from sqlalchemy import Boolean, Column, DateTime, String
from sqlalchemy.sql import func

from app.models.chat_history import Base


class UserAIConfig(Base):
    """每用户一组 AI 模型配置；各列可空，空=回落应用级 .env。api_key 列为加密文本"""

    __tablename__ = "user_ai_config"

    user_id = Column(String(36), primary_key=True)

    chat_base_url = Column(String(255), nullable=True)
    chat_api_key = Column(String(512), nullable=True)
    chat_model = Column(String(128), nullable=True)

    embed_base_url = Column(String(255), nullable=True)
    embed_api_key = Column(String(512), nullable=True)
    embed_model = Column(String(128), nullable=True)

    vision_base_url = Column(String(255), nullable=True)
    vision_api_key = Column(String(512), nullable=True)
    vision_model = Column(String(128), nullable=True)

    rerank_base_url = Column(String(255), nullable=True)
    rerank_api_key = Column(String(512), nullable=True)
    rerank_model = Column(String(128), nullable=True)

    web_search_enabled = Column(Boolean, default=False)
    web_search_api_key = Column(String(512), nullable=True)
    web_search_provider = Column(String(64), nullable=True)

    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
