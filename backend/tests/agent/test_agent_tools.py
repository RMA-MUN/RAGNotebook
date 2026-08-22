"""Agent 工具层测试：LangChain `@tool` 异步函数 + ContextVar 上下文助手。

策略：
- 需要 DB 的工具：`patch_session_factory(monkeypatch, session_factory)` 把工具内
  `AsyncSessionLocal` 指向 SQLite；大部分用例 monkeypatch 具体 service 方法，
  少数用例用 `real_note_service`（真实 NoteService + FakeChromaStore + SQLite）做集成验证。
- 用户上下文：通过 `user_ctx` / `no_user_ctx` fixture 设置并复位 ContextVar。
"""
import asyncio
import re
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest_asyncio
from langchain_core.documents import Document
from sqlalchemy import select

from app.agent import agent_tools as tools
from app.agent.agent_tools import (
    create_note_tool,
    current_user_id_var,
    get_current_user_id_from_context,
    get_note_stats_tool,
    get_related_notes_tool,
    get_thinking_callback_from_context,
    get_today_reviews_tool,
    get_user_info_tools,
    mark_reviewed_tool,
    search_notes_tool,
    set_current_user_id,
    set_thinking_callback,
    what_time_is_now,
)
from app.core.background_init import init_manager
from app.models.note import Note
from app.models.review_record import ReviewRecord
from app.schemas.models import NoteResponse
from app.utils.auth_utils import generate_token
from tests.conftest import install_init_manager_fakes, patch_session_factory
from tests.fakes import make_fake_chat_model

USER_ID = "u1"


# ---------------------------------------------------------------------------
# 本地 fixtures
# ---------------------------------------------------------------------------
@pytest_asyncio.fixture
async def user_ctx():
    """设置当前用户为 USER_ID，测试结束后复位 ContextVar。"""
    token = current_user_id_var.set(USER_ID)
    yield USER_ID
    current_user_id_var.reset(token)


@pytest_asyncio.fixture
async def no_user_ctx():
    """显式清空用户上下文（覆盖其他测试可能残留的值）。"""
    token = current_user_id_var.set(None)
    yield
    current_user_id_var.reset(token)


@pytest_asyncio.fixture
async def patched_db(monkeypatch, session_factory):
    """把工具内部 AsyncSessionLocal 替换为内存 SQLite 会话工厂。"""
    patch_session_factory(monkeypatch, session_factory)
    yield session_factory


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------
def async_returning(value):
    """构造一个直接返回固定值的 async 函数（忽略任意参数）。"""

    async def _f(*args, **kwargs):
        return value

    return _f


# ---------------------------------------------------------------------------
# ContextVar 上下文助手
# ---------------------------------------------------------------------------
async def test_context_helpers_roundtrip():
    """set/get 用户 ID 与 thinking 回调，并验证默认值为 None。"""
    token = current_user_id_var.set(None)
    try:
        assert get_current_user_id_from_context() is None
        set_current_user_id("u42")
        assert get_current_user_id_from_context() == "u42"

        assert get_thinking_callback_from_context() is None
        cb = lambda data: None  # noqa: E731
        set_thinking_callback(cb)
        assert get_thinking_callback_from_context() is cb
    finally:
        current_user_id_var.reset(token)


# ---------------------------------------------------------------------------
# 无依赖工具
# ---------------------------------------------------------------------------
async def test_what_time_is_now_format():
    """返回字符串包含当前 YYYY-MM-DD HH:MM。"""
    out = await what_time_is_now.ainvoke({})
    assert re.fullmatch(r"当前时间是：\d{4}-\d{2}-\d{2} \d{2}:\d{2}", out)


async def test_get_user_info_tools_with_valid_token():
    """合法 JWT（由 generate_token 签发）应解析出 user_id 与用户名。"""
    token, _ = generate_token(USER_ID, "alice", "alice@example.com")
    out = await get_user_info_tools.ainvoke({"token": token})
    assert "用户ID: u1" in out
    assert "用户名: alice" in out


async def test_get_user_info_tools_with_invalid_token():
    """非法 token 返回失败提示。"""
    out = await get_user_info_tools.ainvoke({"token": "this-is-not-a-jwt"})
    assert "无法解析JWT token" in out


# ---------------------------------------------------------------------------
# search_notes_tool
# ---------------------------------------------------------------------------
async def test_search_notes_tool_no_user_context(no_user_ctx, patched_db):
    out = await search_notes_tool.ainvoke({"query": "关键词"})
    assert out == "错误: 无法确定用户身份"


async def test_search_notes_tool_formats_results(monkeypatch, user_ctx, patched_db):
    results = [
        NoteResponse(
            id="n1", user_id=USER_ID, title="FastAPI 笔记",
            content="中间件与依赖注入的配置方法", tags=["python", "fastapi"], category="work",
        ),
        NoteResponse(
            id="n2", user_id=USER_ID, title="LangChain 笔记",
            content="工具调用与编排层设计", tags=None, category=None,
        ),
    ]
    stub = SimpleNamespace(search_notes=async_returning(results))
    monkeypatch.setattr(init_manager, "note_service", stub)

    out = await search_notes_tool.ainvoke({"query": "框架", "top_k": 2})
    assert "找到 2 篇相关笔记" in out
    assert "1. **FastAPI 笔记**" in out
    assert "分类: work" in out
    assert "标签: python, fastapi" in out
    assert "内容预览: 中间件与依赖注入的配置方法..." in out
    assert "2. **LangChain 笔记**" in out


async def test_search_notes_tool_no_results(monkeypatch, user_ctx, patched_db):
    stub = SimpleNamespace(search_notes=async_returning([]))
    monkeypatch.setattr(init_manager, "note_service", stub)
    out = await search_notes_tool.ainvoke({"query": "不存在的东西"})
    assert out == "未找到相关笔记"


async def test_search_notes_tool_error(monkeypatch, user_ctx, patched_db):
    async def boom(db, user_id, query, top_k=5):
        raise RuntimeError("搜索服务不可用")

    stub = SimpleNamespace(search_notes=boom)
    monkeypatch.setattr(init_manager, "note_service", stub)
    out = await search_notes_tool.ainvoke({"query": "x"})
    assert out == "搜索笔记时出错: 搜索服务不可用"


async def test_search_notes_tool_real_service(monkeypatch, user_ctx, patched_db, real_note_service):
    """真实 NoteService + FakeChromaStore + SQLite 的集成路径。"""
    content = "这是一篇用于搜索的真实笔记内容" * 3
    async with patched_db() as db:
        db.add(Note(id="n1", user_id=USER_ID, title="集成测试笔记", content=content, tags=["测试"], category="study"))
        await db.commit()
    real_note_service.notes_store.add_documents(
        [Document(page_content=content, metadata={
            "user_id": USER_ID, "note_id": "n1", "doc_type": "note", "title": "集成测试笔记",
        })],
        ids=["n1"],
    )

    out = await search_notes_tool.ainvoke({"query": "搜索", "top_k": 5})
    assert "找到 1 篇相关笔记" in out
    assert "**集成测试笔记**" in out
    assert "分类: study" in out
    assert "标签: 测试" in out


# ---------------------------------------------------------------------------
# get_note_stats_tool
# ---------------------------------------------------------------------------
async def test_get_note_stats_tool_no_user(no_user_ctx, patched_db):
    out = await get_note_stats_tool.ainvoke({})
    assert out == "错误: 无法确定用户身份"


async def test_get_note_stats_tool_formats(monkeypatch, user_ctx, patched_db):
    stats = {
        "total": 5,
        "categories": [
            {"category": "work", "count": 2},
            {"category": "study", "count": 1},
            {"category": "other", "count": 1},
        ],
        "uncategorized": 1,
    }
    stub = SimpleNamespace(get_category_stats=async_returning(stats))
    monkeypatch.setattr(init_manager, "note_service", stub)

    out = await get_note_stats_tool.ainvoke({})
    assert "📊 笔记统计" in out
    assert "总笔记数: 5" in out
    assert "💼 work: 2 篇" in out
    assert "📖 study: 1 篇" in out
    assert "📄 other: 1 篇" in out
    assert "📄 未分类: 1 篇" in out


async def test_get_note_stats_tool_error(monkeypatch, user_ctx, patched_db):
    async def boom(db, user_id):
        raise RuntimeError("统计查询失败")

    stub = SimpleNamespace(get_category_stats=boom)
    monkeypatch.setattr(init_manager, "note_service", stub)
    out = await get_note_stats_tool.ainvoke({})
    assert out == "获取笔记统计时出错: 统计查询失败"


async def test_get_note_stats_tool_real(monkeypatch, user_ctx, patched_db, real_note_service):
    async with patched_db() as db:
        db.add_all([
            Note(id="n1", user_id=USER_ID, title="a", content="c", category="work"),
            Note(id="n2", user_id=USER_ID, title="b", content="c", category="work"),
            Note(id="n3", user_id=USER_ID, title="c", content="c", category=None),
        ])
        await db.commit()

    out = await get_note_stats_tool.ainvoke({})
    assert "总笔记数: 3" in out
    assert "💼 work: 2 篇" in out
    assert "📄 未分类: 1 篇" in out


# ---------------------------------------------------------------------------
# get_today_reviews_tool
# ---------------------------------------------------------------------------
async def test_get_today_reviews_tool_no_user(no_user_ctx, patched_db):
    out = await get_today_reviews_tool.ainvoke({})
    assert out == "错误: 无法确定用户身份"


async def test_get_today_reviews_tool_empty(monkeypatch, user_ctx, patched_db):
    stub = SimpleNamespace(get_today_reviews=async_returning([]))
    monkeypatch.setattr(tools, "review_service", stub)
    out = await get_today_reviews_tool.ainvoke({})
    assert out == "今日没有待回顾的笔记，继续保持！"


async def test_get_today_reviews_tool_formats(monkeypatch, user_ctx, patched_db):
    reviews = [{
        "review_id": "r1", "note_id": "n1", "title": "Python 复习",
        "content_preview": "可变对象与不可变对象的区别", "tags": ["python"],
        "category": "study", "review_count": 2,
        "last_reviewed_at": "2026-01-01 00:00:00", "interval_days": 7,
    }]
    stub = SimpleNamespace(get_today_reviews=async_returning(reviews))
    monkeypatch.setattr(tools, "review_service", stub)

    out = await get_today_reviews_tool.ainvoke({})
    assert "📅 今日待回顾笔记（共 1 篇）" in out
    assert "1. **Python 复习**" in out
    assert "回顾次数: 第 3 次" in out
    assert "内容预览: 可变对象与不可变对象的区别..." in out


async def test_get_today_reviews_tool_error(monkeypatch, user_ctx, patched_db):
    async def boom(db, user_id):
        raise RuntimeError("回顾查询失败")

    stub = SimpleNamespace(get_today_reviews=boom)
    monkeypatch.setattr(tools, "review_service", stub)
    out = await get_today_reviews_tool.ainvoke({})
    assert out == "获取今日回顾时出错: 回顾查询失败"


async def test_get_today_reviews_tool_real(monkeypatch, user_ctx, patched_db):
    now = datetime.now()
    async with patched_db() as db:
        db.add(Note(id="n1", user_id=USER_ID, title="间隔复习", content="艾宾浩斯遗忘曲线", category="study"))
        await db.commit()
        db.add(ReviewRecord(
            id="r1", note_id="n1", user_id=USER_ID, review_count=2, interval_days=7,
            last_reviewed_at=now - timedelta(days=7), next_review_at=now - timedelta(hours=1),
        ))
        await db.commit()

    out = await get_today_reviews_tool.ainvoke({})
    assert "📅 今日待回顾笔记（共 1 篇）" in out
    assert "1. **间隔复习**" in out
    assert "第 3 次" in out


# ---------------------------------------------------------------------------
# mark_reviewed_tool
# ---------------------------------------------------------------------------
async def test_mark_reviewed_tool_no_user(no_user_ctx, patched_db):
    out = await mark_reviewed_tool.ainvoke({"note_id": "n1"})
    assert out == "错误: 无法确定用户身份"


async def test_mark_reviewed_tool_success(monkeypatch, user_ctx, patched_db):
    stub = SimpleNamespace(mark_reviewed=async_returning({
        "success": True, "message": "已标记回顾", "review_count": 3, "interval_days": 7,
    }))
    monkeypatch.setattr(tools, "review_service", stub)
    out = await mark_reviewed_tool.ainvoke({"note_id": "n1"})
    assert out == "✅ 已标记回顾完成！第 3 次回顾，下次回顾间隔 7 天。"


async def test_mark_reviewed_tool_failure(monkeypatch, user_ctx, patched_db):
    stub = SimpleNamespace(mark_reviewed=async_returning({
        "success": False, "message": "回顾记录不存在",
    }))
    monkeypatch.setattr(tools, "review_service", stub)
    out = await mark_reviewed_tool.ainvoke({"note_id": "n1"})
    assert out == "标记失败: 回顾记录不存在"


async def test_mark_reviewed_tool_error(monkeypatch, user_ctx, patched_db):
    async def boom(db, note_id, user_id):
        raise RuntimeError("标记失败数据库错误")

    stub = SimpleNamespace(mark_reviewed=boom)
    monkeypatch.setattr(tools, "review_service", stub)
    out = await mark_reviewed_tool.ainvoke({"note_id": "n1"})
    assert out == "标记回顾时出错: 标记失败数据库错误"


async def test_mark_reviewed_tool_real(monkeypatch, user_ctx, patched_db):
    now = datetime.now()
    async with patched_db() as db:
        db.add(Note(id="n1", user_id=USER_ID, title="笔记", content="内容"))
        await db.commit()
        db.add(ReviewRecord(
            id="r1", note_id="n1", user_id=USER_ID, review_count=0, interval_days=1,
            last_reviewed_at=None, next_review_at=now,
        ))
        await db.commit()

    out = await mark_reviewed_tool.ainvoke({"note_id": "n1"})
    assert out == "✅ 已标记回顾完成！第 1 次回顾，下次回顾间隔 2 天。"

    async with patched_db() as db:
        r = (await db.execute(select(ReviewRecord).where(ReviewRecord.note_id == "n1"))).scalar_one()
    assert r.review_count == 1
    assert r.interval_days == 2


# ---------------------------------------------------------------------------
# create_note_tool
# ---------------------------------------------------------------------------
async def test_create_note_tool_no_user(no_user_ctx, patched_db):
    out = await create_note_tool.ainvoke({"title": "新笔记"})
    assert out == "错误: 无法确定用户身份"


async def test_create_note_tool_success(monkeypatch, user_ctx, patched_db):
    fake_note = SimpleNamespace(title="新笔记", id="n123")
    stub = SimpleNamespace(create_note=async_returning(fake_note))
    monkeypatch.setattr(init_manager, "note_service", stub)

    out = await create_note_tool.ainvoke({"title": "新笔记", "content": "正文内容"})
    assert out == "✅ 笔记创建成功！\n- 标题: 新笔记\n- ID: n123\n- 标签和分类正在后台生成中..."


async def test_create_note_tool_error(monkeypatch, user_ctx, patched_db):
    async def boom(db, user_id, payload):
        raise RuntimeError("笔记服务不可用")

    stub = SimpleNamespace(create_note=boom)
    monkeypatch.setattr(init_manager, "note_service", stub)
    out = await create_note_tool.ainvoke({"title": "新笔记"})
    assert out == "创建笔记时出错: 笔记服务不可用"


async def test_create_note_tool_real(monkeypatch, user_ctx, patched_db, real_note_service):
    """真实 NoteService：SQLite 落库 + FakeChromaStore 向量 + 假模型后台自动标签。"""
    install_init_manager_fakes(monkeypatch, chat_model=make_fake_chat_model())

    out = await create_note_tool.ainvoke({"title": "真实创建的笔记", "content": "正文内容"})
    assert "✅ 笔记创建成功！" in out
    assert "- 标题: 真实创建的笔记" in out
    note_id = out.split("- ID: ")[1].split("\n")[0].strip()

    async with patched_db() as db:
        n = (await db.execute(select(Note).where(Note.id == note_id))).scalar_one()
    assert n.user_id == USER_ID
    assert n.title == "真实创建的笔记"

    # 让后台自动标签任务（假模型）有机会收尾，避免 pending 任务告警
    await asyncio.sleep(0.05)


# ---------------------------------------------------------------------------
# get_related_notes_tool
# ---------------------------------------------------------------------------
async def test_get_related_notes_tool_no_user(no_user_ctx, patched_db):
    out = await get_related_notes_tool.ainvoke({"note_id": "n1"})
    assert out == "错误: 无法确定用户身份"


async def test_get_related_notes_tool_formats(monkeypatch, user_ctx, patched_db):
    related = [
        {"source": "note", "title": "相似笔记A", "similarity": 0.42, "content_preview": "内容A预览"},
        {"source": "knowledge_base", "title": "知识文档B", "similarity": 0.55, "content_preview": "内容B预览"},
    ]
    stub = SimpleNamespace(get_related_notes=async_returning(related))
    monkeypatch.setattr(init_manager, "note_service", stub)

    out = await get_related_notes_tool.ainvoke({"note_id": "n1", "top_k": 3})
    assert "🔗 关联推荐（共 2 项）" in out
    assert "1. 📝 笔记 — 相似笔记A" in out
    assert "相似度: 0.42" in out
    assert "预览: 内容A预览..." in out
    assert "2. 📚 知识库 — 知识文档B" in out


async def test_get_related_notes_tool_no_results(monkeypatch, user_ctx, patched_db):
    stub = SimpleNamespace(get_related_notes=async_returning([]))
    monkeypatch.setattr(init_manager, "note_service", stub)
    out = await get_related_notes_tool.ainvoke({"note_id": "n1"})
    assert out == "未找到关联笔记或知识库文档"


async def test_get_related_notes_tool_error(monkeypatch, user_ctx, patched_db):
    async def boom(db, note_id, user_id, top_k=3):
        raise RuntimeError("关联推荐失败")

    stub = SimpleNamespace(get_related_notes=boom)
    monkeypatch.setattr(init_manager, "note_service", stub)
    out = await get_related_notes_tool.ainvoke({"note_id": "n1"})
    assert out == "获取关联推荐时出错: 关联推荐失败"


async def test_get_related_notes_tool_real(monkeypatch, user_ctx, patched_db, real_note_service):
    """真实 NoteService + FakeChromaStore（笔记侧）+ FakeVectorStoreService（知识库侧）。"""
    from tests.conftest import install_fake_vector_store

    install_fake_vector_store(monkeypatch)

    content1 = "机器学习与深度学习的基础概念"
    content2 = "机器学习进阶算法"
    async with patched_db() as db:
        db.add(Note(id="n1", user_id=USER_ID, title="锚点笔记", content=content1))
        db.add(Note(id="n2", user_id=USER_ID, title="相似笔记", content=content2))
        await db.commit()

    store = real_note_service.notes_store
    store.add_documents([
        Document(page_content=content1, metadata={"user_id": USER_ID, "note_id": "n1", "doc_type": "note", "title": "锚点笔记"}),
        Document(page_content=content2, metadata={"user_id": USER_ID, "note_id": "n2", "doc_type": "note", "title": "相似笔记"}),
    ], ids=["n1", "n2"])

    out = await get_related_notes_tool.ainvoke({"note_id": "n1", "top_k": 3})
    assert "🔗 关联推荐（共 1 项）" in out
    assert "1. 📝 笔记 — 相似笔记" in out
    assert "相似度: 0.5" in out
