import uuid as _uuid

from sqlalchemy import Column, Integer, String, Text, DateTime, SmallInteger
from sqlalchemy.sql import func

from app.models.chat_history import Base


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, autoincrement=True)
    uuid = Column(String(36), unique=True, index=True, nullable=False)
    username = Column(String(150), nullable=False)
    email = Column(String(254), unique=True, nullable=False)
    telephone = Column(String(11), unique=True, nullable=True)
    hashed_password = Column(String(255), nullable=False)

    # 用户状态: 1=ACTIVE, 2=LOCKED, 0=DISABLED
    status = Column(SmallInteger, default=1, nullable=False)

    gender = Column(SmallInteger, nullable=True)
    bio = Column(Text, nullable=True)
    avatar = Column(String(255), nullable=True)

    date_joined = Column(DateTime(timezone=True), server_default=func.now())
    last_login = Column(DateTime(timezone=True), nullable=True)

    @staticmethod
    def generate_uuid() -> str:
        return str(_uuid.uuid4())
