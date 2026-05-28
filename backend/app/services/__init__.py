from app.services.database_session_manager import DatabaseSessionManager, database_session_manager


# 创建一个代理对象，确保能够访问到初始化后的 database_session_manager
class SessionManagerProxy:
    @property
    def session_manager(self):
        return database_session_manager

session_manager = SessionManagerProxy()

__all__ = ["session_manager", "DatabaseSessionManager"]
