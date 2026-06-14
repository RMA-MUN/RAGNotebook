import asyncio

from app.core.logger_handler import logger
from app.db.db_config import AsyncSessionLocal
from app.models.chat_history import ChatMessage, ChatSession


class DatabaseSessionManager:
    """基于数据库的会话管理器"""

    def __init__(self):
        """初始化会话管理器"""
        self._lock = asyncio.Lock()

    @classmethod
    async def create(cls) -> "DatabaseSessionManager":
        """
        异步创建并初始化 DatabaseSessionManager
        :return: 初始化完成的 DatabaseSessionManager 实例
        """
        instance = cls()
        logger.info("【数据库会话管理】初始化完成")
        return instance

    async def get_session(self, session_id: str, user_id: str) -> dict:
        """获取会话"""
        async with AsyncSessionLocal() as db:
            # 尝试查找会话，验证属于该用户
            result = await db.run_sync(
                lambda session: session.query(ChatSession).filter(ChatSession.id == session_id, ChatSession.user_id == user_id).first()
            )

            if result:
                # 获取会话历史
                messages = await db.run_sync(
                    lambda session: session.query(ChatMessage).filter(ChatMessage.session_id == result.id).order_by(ChatMessage.created_at).all()
                )
                # 转换为 (user_message, assistant_message) 格式
                history = []
                i = 0
                while i < len(messages):
                    if messages[i].role == "user" and i + 1 < len(messages) and messages[i+1].role == "assistant":
                        history.append((messages[i].content, messages[i+1].content))
                        i += 2
                    else:
                        i += 1
                return {"history": history}
            else:
                # 检查会话id是否存在
                existing_session = await db.run_sync(
                    lambda session: session.query(ChatSession).filter(ChatSession.id == session_id).first()
                )

                if existing_session:
                    # 会话存在但不属于当前用户
                    logger.warning(f"【数据库会话管理】会话 {session_id} 不属于用户 {user_id}")
                    from fastapi import HTTPException, status
                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN,
                        detail="当前会话不属于你"
                    )
                else:
                    # 会话不存在，创建一个新的
                    new_session = ChatSession(
                        id=session_id,
                        user_id=user_id,
                        title="新的对话"
                    )
                    db.add(new_session)
                    await db.commit()
                    await db.refresh(new_session)
                    logger.info(f"【数据库会话管理】创建新会话: {session_id} 属于用户: {user_id}")
                    return {"history": []}

    async def add_message(self, session_id: str, user_id: str, user_message: str, assistant_message: str):
        """添加消息并保存到数据库"""
        async with AsyncSessionLocal() as db:
            # 检查会话id是否存在
            existing_session = await db.run_sync(
                lambda session: session.query(ChatSession).filter(ChatSession.id == session_id).first()
            )

            if existing_session:
                # 检查会话是否属于当前用户
                if existing_session.user_id != user_id:
                    # 会话存在但不属于当前用户，不添加消息
                    logger.warning(f"【数据库会话管理】会话 {session_id} 不属于用户 {user_id}，无法添加消息")
                    from fastapi import HTTPException, status
                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN,
                        detail="当前会话不属于你，无法添加消息"
                    )
                session = existing_session
            else:
                # 会话不存在，创建一个新的
                session = ChatSession(
                    id=session_id,
                    user_id=user_id,
                    title="新的对话"
                )
                db.add(session)
                await db.commit()
                await db.refresh(session)

            # 检查是否是新会话且标题为默认值，如果是则更新为用户的第一个问题
            if session.title == "新的对话":
                # 生成用户问题的摘要作为标题（截取前30个字符）
                title_summary = user_message[:30].strip()
                if len(user_message) > 30:
                    title_summary += "..."
                session.title = title_summary

            # 添加用户消息
            user_msg = ChatMessage(
                session_id=session.id,
                role="user",
                content=user_message
            )
            db.add(user_msg)

            # 添加助手消息
            assistant_msg = ChatMessage(
                session_id=session.id,
                role="assistant",
                content=assistant_message
            )
            db.add(assistant_msg)

            await db.commit()
            logger.info(f"【数据库会话管理】添加消息到会话: {session_id} 属于用户: {user_id}")

    async def get_history(self, session_id: str, user_id: str) -> list[tuple[str, str]]:
        """获取会话历史"""
        session_data = await self.get_session(session_id, user_id)
        return session_data.get("history", [])

    async def clear_session(self, session_id: str, user_id: str):
        """清除会话"""
        async with AsyncSessionLocal() as db:
            # 查找会话，验证属于该用户
            session = await db.run_sync(
                lambda session: session.query(ChatSession).filter(ChatSession.id == session_id, ChatSession.user_id == user_id).first()
            )

            if session:
                # 删除会话（级联删除消息）
                await db.delete(session)
                await db.commit()
                logger.info(f"【数据库会话管理】会话 {session_id} 已清除，属于用户: {user_id}")

    async def get_all_session_ids(self, user_id: str | None = None) -> list[str]:
        """获取所有会话 ID，如果提供了 user_id，则只返回该用户的会话"""
        async with AsyncSessionLocal() as db:
            if user_id:
                sessions = await db.run_sync(
                    lambda session: session.query(ChatSession).filter(ChatSession.user_id == user_id).all()
                )
            else:
                sessions = await db.run_sync(
                    lambda session: session.query(ChatSession).all()
                )
            return [session.id for session in sessions]

    async def get_user_sessions(self, user_id: str) -> list[dict]:
        """获取用户所有会话详细信息，按更新时间降序排列"""
        async with AsyncSessionLocal() as db:
            sessions = await db.run_sync(
                lambda session: session.query(ChatSession)
                .filter(ChatSession.user_id == user_id)
                .order_by(ChatSession.updated_at.desc())
                .all()
            )
            return [
                {
                    "id": session.id,
                    "title": session.title,
                    "created_at": session.created_at.isoformat() if session.created_at else None,
                    "updated_at": session.updated_at.isoformat() if session.updated_at else None
                }
                for session in sessions
            ]


# 全局数据库会话管理器实例
database_session_manager = None

# 初始化数据库会话管理器
async def init_database_session_manager():
    """
    初始化数据库会话管理器
    :return: 初始化完成的 DatabaseSessionManager 实例
    """
    global database_session_manager
    database_session_manager = await DatabaseSessionManager.create()
    return database_session_manager
