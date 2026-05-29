from app.services.database_session_manager import DatabaseSessionManager, database_session_manager


class SessionManagerProxy:
    """代理对象，确保访问时 database_session_manager 已被初始化"""

    @property
    def session_manager(self) -> DatabaseSessionManager:
        global database_session_manager
        if database_session_manager is None:
            database_session_manager = DatabaseSessionManager()
        return database_session_manager


session_manager = SessionManagerProxy()

__all__ = ["session_manager", "DatabaseSessionManager"]
