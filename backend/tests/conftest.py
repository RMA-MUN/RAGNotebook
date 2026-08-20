"""pytest 共享基础设施。

统一测试策略（全 Mock，不依赖外部服务）：
- MySQL      -> 文件型 SQLite（每 session 独立连接，贴近真实 MySQL 连接池）
- Redis      -> tests/fakes.FakeRedis
- ChromaDB   -> tests/fakes.FakeChromaStore（真实 NoteService 逻辑照常执行）
- LLM        -> tests/fakes.make_fake_chat_model（GenericFakeChatModel）
- 重排序模型 -> tests/fakes.FakeReorderService
- API 测试   -> httpx ASGITransport 直连 FastAPI 应用，不进入 lifespan（不触发启动连库）

注意：本模块在导入任何 app.* 模块之前设置测试环境变量（SECRET_KEY 等），
依赖 app 模块 import 时读取这些变量。
"""
import os
import sys
from pathlib import Path

# ---- 环境变量：必须在任何 app.* import 之前设置 ----
os.environ.setdefault("SECRET_KEY", "pytest-secret-key-for-tests")
os.environ.setdefault("ALGORITHM", "HS256")
os.environ["RATE_LIMIT_ENABLED"] = "false"
# 避免测试触发真实视觉模型/重排序模型的初始化
os.environ.setdefault("VISION_ENABLED", "false")

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event as sa_event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models.chat_history import Base

from tests.fakes import (
    FakeChromaStore,
    FakeReorderService,
    FakeVectorStoreService,
    TEST_USER_ID,
    install_fake_redis,
    make_fake_chat_model,
)

# 方便各测试文件直接复用（例如替换 VectorStoreService 时）
__all__ = [
    "FakeChromaStore",
    "FakeReorderService",
    "FakeVectorStoreService",
    "TEST_USER_ID",
    "install_fake_redis",
    "make_fake_chat_model",
]

# backend 目录加入 sys.path，保证 `from app...` 可导入（tests/__init__.py 已存在，通常已生效）
BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


# ---------------------------------------------------------------------------
# 数据库 fixtures（文件型 SQLite）
# ---------------------------------------------------------------------------
@pytest_asyncio.fixture
async def db_engine(tmp_path):
    """每个测试独立的文件型 SQLite 引擎（每 session 独立连接，贴近真实 MySQL 连接池）。

    注意：先前用内存库 + StaticPool，所有 session 共享同一条物理连接。
    后台任务（如自动标签）与测试轮询 session 并发时，
    两条匿名事务在共享连接上互相踩踏，导致任务 UPDATE 被回滚。
    改用文件库 + 默认连接池后，每个 session 有独立连接，写操作可被其他连接看到（与生产一致）。
    """
    # 确保所有 Model 在 create_all 之前已注册到 Base.metadata
    from app.models import chat_history, note, note_template, review_record, user_model  # noqa: F401

    db_file = tmp_path / "test.db"
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{db_file}",
        connect_args={"check_same_thread": False},
    )

    @sa_event.listens_for(engine.sync_engine, "connect")
    def _enable_fk(dbapi_conn, _record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def session_factory(db_engine):
    """async_sessionmaker 工厂。"""
    return async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)


@pytest_asyncio.fixture
async def db_session(session_factory):
    """单个数据库会话（服务层测试用）。"""
    async with session_factory() as session:
        yield session


def patch_session_factory(monkeypatch, factory):
    """把所有模块内 `from app.db.db_config import AsyncSessionLocal` 的引用替换为 SQLite 工厂。

    必须在每次 API 测试前调用，否则 router/服务会连接真实 MySQL。

    注意：`app.services.database_session_manager` 作为 `app.services.xxx` 属性访问会被
    __init__.py 的同名全局变量（初始 None）遮蔽，必须用 importlib.import_module 取真实模块。
    """
    import importlib

    targets = {
        "app.db.db_config": "AsyncSessionLocal",
        "app.router.user": "AsyncSessionLocal",
        "app.services.database_session_manager": "AsyncSessionLocal",
        "app.agent.agent_tools": "AsyncSessionLocal",
        "app.utils.auth_utils": "AsyncSessionLocal",
    }
    for module_name, attr_name in targets.items():
        monkeypatch.setattr(importlib.import_module(module_name), attr_name, factory)


# ---------------------------------------------------------------------------
# 后台初始化 & 模型 fakes
# ---------------------------------------------------------------------------
def install_init_manager_fakes(
    monkeypatch,
    chat_model=None,
    note_service=None,
    reorder_service=None,
    embed_model=None,
):
    """给 init_manager 注入假模型/服务，并置位所有 readiness Event。"""
    from app.core.background_init import init_manager

    if chat_model is not None:
        monkeypatch.setattr(init_manager, "chat_model", chat_model)
    if embed_model is not None:
        monkeypatch.setattr(init_manager, "embed_model", embed_model)
    if note_service is not None:
        monkeypatch.setattr(init_manager, "note_service", note_service)
    if reorder_service is not None:
        monkeypatch.setattr(init_manager, "reorder_service", reorder_service)

    for event_name in ("models_ready", "note_service_ready", "reranker_ready"):
        getattr(init_manager, event_name).set()
    return init_manager


@pytest_asyncio.fixture
async def fake_models(monkeypatch):
    """默认：假 chat/embed 模型 + 假 reorder 服务 + 空 note_service 占位。"""
    chat_model = make_fake_chat_model()
    reorder_service = FakeReorderService()
    install_init_manager_fakes(
        monkeypatch,
        chat_model=chat_model,
        reorder_service=reorder_service,
    )
    return {"chat_model": chat_model, "reorder_service": reorder_service}


@pytest_asyncio.fixture
async def real_note_service(monkeypatch):
    """真实 NoteService + FakeChromaStore（向量层替身），并挂到 init_manager。"""
    import app.services.note_service as note_service_module
    from app.services.note_service import NoteService

    monkeypatch.setattr(note_service_module, "Chroma", lambda **kwargs: FakeChromaStore())

    service = NoteService(embed_model=None)
    install_init_manager_fakes(monkeypatch, note_service=service)
    return service


# ---------------------------------------------------------------------------
# API 测试 fixtures
# ---------------------------------------------------------------------------
@pytest_asyncio.fixture
async def client(session_factory, monkeypatch, fake_models):
    """FastAPI 应用 + SQLite + 内存 Redis + 假模型的异步测试客户端。

    使用 httpx ASGITransport 直连应用、不触发 lifespan（启动事件不会执行，
    因此不会连接 MySQL/Redis）。
    """
    from main import app
    from app.utils.auth_utils import get_current_user_id, security
    from fastapi.security import HTTPAuthorizationCredentials

    # 1. 数据库：依赖注入 get_db → SQLite；模块内 AsyncSessionLocal → SQLite
    from app.db.db_config import get_db

    async def _override_get_db():
        async with session_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise
            finally:
                await session.close()

    patch_session_factory(monkeypatch, session_factory)

    # 2. 认证：固定 user_id；HTTPBearer 返回占位凭据
    async def _override_user_id():
        return TEST_USER_ID

    def _override_security():
        return HTTPAuthorizationCredentials(scheme="Bearer", credentials="dummy-token")

    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_current_user_id] = _override_user_id
    app.dependency_overrides[security] = _override_security

    # 3. Redis
    await install_fake_redis(monkeypatch)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as c:
        yield c

    app.dependency_overrides.clear()


@pytest.fixture
def auth_headers():
    """携带 JWT 的请求头（dependency_overrides 已固定 user_id，token 内容不重要）。"""
    return {"Authorization": "Bearer dummy-token"}


@pytest_asyncio.fixture
async def raw_client(session_factory, monkeypatch):
    """不带认证 overrides 的客户端：用来验证真实 security / get_current_user_id 行为。

    - 无 Authorization 头 → HTTPBearer 403
    - 非法 Token → 401（get_current_user_id 内部逻辑）
    - 黑名单 Token → 401
    """
    from main import app

    from app.db.db_config import get_db

    patch_session_factory(monkeypatch, session_factory)
    await install_fake_redis(monkeypatch)

    async def _override_get_db():
        async with session_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise
            finally:
                await session.close()

    app.dependency_overrides[get_db] = _override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as c:
        yield c
    app.dependency_overrides.clear()


def install_fake_vector_store(monkeypatch, route_score: float = 0.0, documents=None):
    """把 app.rag.vector_store.VectorStoreService 整体替换为内存替身。"""
    import app.rag.vector_store as vector_store_module

    state = {"instance": None}

    def _factory(*args, **kwargs):
        if state["instance"] is None:
            state["instance"] = FakeVectorStoreService(route_score=route_score, documents=documents)
        return state["instance"]

    monkeypatch.setattr(vector_store_module, "VectorStoreService", _factory)
    return state["instance"] or FakeVectorStoreService(route_score=route_score, documents=documents)