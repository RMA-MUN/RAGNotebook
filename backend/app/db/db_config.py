import os

from dotenv import load_dotenv
from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.logger_handler import logger
from app.models.chat_history import Base

# 加载环境变量
load_dotenv()

# 数据库URL
ASYNC_DATABSE_URL = (
    f"mysql+aiomysql://{os.getenv('MYSQL_USER', 'root')}:{os.getenv('MYSQL_PASSWORD', '')}"
    f"@{os.getenv('MYSQL_HOST', 'localhost')}:{os.getenv('MYSQL_PORT', '3306')}"
    f"/{os.getenv('MYSQL_DATABASE', 'chat_history')}?charset=utf8mb4"
)

# 创建异步引擎
async_engine = create_async_engine(
    ASYNC_DATABSE_URL,
    pool_size=10, # 连接池中保持的持久连接数
    max_overflow=20, # 连接池中允许创建的额外连接数
    echo=False # 输出sql日志
)

# 创建异步会话工厂
AsyncSessionLocal = async_sessionmaker(
    bind=async_engine,
    class_=AsyncSession,
    expire_on_commit=False
)

# SQLAlchemy 类型 → MySQL DDL 映射
_MYSQL_TYPE_MAP = {
    "String": lambda col: f"VARCHAR({col.type.length or 255})",
    "Text": lambda col: "TEXT",
    "Boolean": lambda col: "TINYINT(1) NOT NULL DEFAULT 0",
    "Integer": lambda col: "INT",
    "Float": lambda col: "DOUBLE",
    "JSON": lambda col: "JSON",
    "DateTime": lambda col: "DATETIME",
}


def _get_mysql_type_ddl(col):
    type_name = type(col.type).__name__
    mapper = _MYSQL_TYPE_MAP.get(type_name)
    if mapper:
        return mapper(col)
    return "TEXT"


async def _migrate_columns(conn):
    """检查所有已注册表，自动补全缺失的列。"""
    def _check(sync_conn):
        inspector = inspect(sync_conn)
        existing_tables = inspector.get_table_names()
        for table in Base.metadata.sorted_tables:
            if table.name not in existing_tables:
                continue
            existing_cols = {c["name"] for c in inspector.get_columns(table.name)}
            for col in table.columns:
                if col.name not in existing_cols:
                    ddl = _get_mysql_type_ddl(col)
                    nullable = "" if "NOT NULL" in ddl else " NULL"
                    default = ""
                    if col.default is not None and col.default.is_scalar:
                        default = f" DEFAULT {repr(col.default.arg)}"
                    sql = f"ALTER TABLE {table.name} ADD COLUMN {col.name} {ddl}{nullable}{default}"
                    print(f"[migrate] {sql}")
                    sync_conn.execute(text(sql))
    await conn.run_sync(_check)


# 初始化数据库，创建所有表
async def init_db():
    # 确保所有 Model 已导入，注册到 Base.metadata
    from app.models import chat_history, note, note_template, review_record, user_model  # noqa: F401

    async with async_engine.begin() as conn:
        # 先删除旧表，然后创建新表
        # await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
        await _migrate_columns(conn)

# 依赖项
async def get_db():
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()

        except Exception:
            await session.rollback()
            raise

        finally:
            await session.close()




async def seed_test_user():
    from app.models.user_model import User, UserStatusChoice
    from app.utils.auth_utils import hash_password
    from sqlalchemy import select

    async with AsyncSessionLocal() as session:
        result = await session.execute(select(User).where(User.username == "admin"))
        if result.scalar_one_or_none():
            logger.info("测试用户 admin 已存在，跳过创建")
            return

        user = User(
            username="admin",
            email="admin@example.com",
            password=hash_password("admin1234"),
            status=UserStatusChoice.ACTIVE,
            is_active=True,
        )
        session.add(user)
        await session.commit()
        logger.info("测试用户 admin / admin1234 已自动创建")


async def check_mysql_connection() -> bool:
    """检查MySQL连接"""
    try:
        async with async_engine.connect() as conn:
            # 执行简单查询
            await conn.execute(text("SELECT 1"))
        return True
    except Exception as e:
        print(f"MySQL连接失败: {e}")
        return False
