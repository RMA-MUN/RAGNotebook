"""DatabaseSessionManager 服务层测试 —— 真实逻辑 + SQLite 内存库。

注意：`app.services.database_session_manager` 作为属性会被 __init__.py 的全局变量遮蔽，
必须用 from-import（本文件采用）或 importlib.import_module 获取真实模块。
"""
import uuid
from datetime import datetime, timedelta

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.models.chat_history import ChatMessage, ChatSession
from app.services.database_session_manager import DatabaseSessionManager

from tests.conftest import patch_session_factory


def _uid(prefix: str = "dbm") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


async def _create_session(factory, session_id: str, user_id: str, title: str = "新的对话"):
    async with factory() as db:
        session = ChatSession(id=session_id, user_id=user_id, title=title)
        db.add(session)
        await db.commit()
        await db.refresh(session)
        return session


async def _add_messages(factory, session_id: str, pairs: list[tuple[str, str]]):
    async with factory() as db:
        for role, content in pairs:
            db.add(ChatMessage(session_id=session_id, role=role, content=content))
        await db.commit()


async def _count_rows(factory, model, **filters):
    async with factory() as db:
        stmt = select(model)
        for col, val in filters.items():
            stmt = stmt.where(getattr(model, col) == val)
        result = await db.execute(stmt)
        return len(result.scalars().all())


@pytest.fixture
def mgr(session_factory, monkeypatch):
    """已把 AsyncSessionLocal 替换为 SQLite 工厂的 DatabaseSessionManager。"""
    patch_session_factory(monkeypatch, session_factory)
    return DatabaseSessionManager()


# ---------------------------------------------------------------------------
# get_session
# ---------------------------------------------------------------------------
async def test_get_session_creates_new_session_when_missing(mgr, session_factory):
    sid, user = _uid(), _uid("user")

    data = await mgr.get_session(sid, user)

    assert data == {"history": []}
    async with session_factory() as db:
        row = (await db.execute(select(ChatSession).where(ChatSession.id == sid))).scalar_one()
        assert row.user_id == user
        assert row.title == "新的对话"


async def test_get_session_returns_paired_history(mgr, session_factory):
    sid, user = _uid(), _uid("user")
    await _create_session(session_factory, sid, user)
    await _add_messages(session_factory, sid, [("user", "你好"), ("assistant", "你好！"), ("user", "再问一个"), ("assistant", "好的")])

    data = await mgr.get_session(sid, user)
    assert data["history"] == [("你好", "你好！"), ("再问一个", "好的")]


async def test_get_session_skips_unpaired_messages(mgr, session_factory):
    sid, user = _uid(), _uid("user")
    await _create_session(session_factory, sid, user)
    await _add_messages(session_factory, sid, [("user", "u1"), ("assistant", "a1"), ("user", "孤立")])

    data = await mgr.get_session(sid, user)
    assert data["history"] == [("u1", "a1")]


async def test_get_session_raises_403_for_other_users_session(mgr, session_factory):
    sid, owner, other = _uid(), _uid("owner"), _uid("other")
    await _create_session(session_factory, sid, owner, title="别人的会话")

    with pytest.raises(HTTPException) as exc:
        await mgr.get_session(sid, other)
    assert exc.value.status_code == 403


# ---------------------------------------------------------------------------
# add_message
# ---------------------------------------------------------------------------
async def test_add_message_creates_session_and_titles_with_summary(mgr, session_factory):
    sid, user = _uid(), _uid("user")
    long_msg = "这是一段超过三十个字符的用户提问内容用于验证标题截断逻辑是否生效"
    assert len(long_msg) > 30

    await mgr.add_message(sid, user, long_msg, "好的，这是回答")

    expected_title = long_msg[:30].strip() + "..."
    async with session_factory() as db:
        session = (await db.execute(select(ChatSession).where(ChatSession.id == sid))).scalar_one()
        assert session.title == expected_title
        assert session.user_id == user
        messages = (await db.execute(select(ChatMessage).where(ChatMessage.session_id == sid).order_by(ChatMessage.created_at))).scalars().all()
        assert [(m.role, m.content) for m in messages] == [("user", long_msg), ("assistant", "好的，这是回答")]

    assert await mgr.get_history(sid, user) == [(long_msg, "好的，这是回答")]


async def test_add_message_short_message_title_without_ellipsis(mgr, session_factory):
    sid, user = _uid(), _uid("user")
    msg_30 = "x" * 30  # 恰好 30 字符：不追加省略号

    await mgr.add_message(sid, user, msg_30, "回答")

    async with session_factory() as db:
        session = (await db.execute(select(ChatSession).where(ChatSession.id == sid))).scalar_one()
        assert session.title == msg_30


async def test_add_message_keeps_title_on_follow_up(mgr, session_factory):
    sid, user = _uid(), _uid("user")
    await mgr.add_message(sid, user, "第一个问题", "第一个回答")

    await mgr.add_message(sid, user, "第二个问题", "第二个回答")

    async with session_factory() as db:
        session = (await db.execute(select(ChatSession).where(ChatSession.id == sid))).scalar_one()
        assert session.title == "第一个问题"  # 标题只由第一条消息决定

    assert await mgr.get_history(sid, user) == [("第一个问题", "第一个回答"), ("第二个问题", "第二个回答")]


async def test_add_message_raises_403_for_other_users_session(mgr, session_factory):
    sid, owner, other = _uid(), _uid("owner"), _uid("other")
    await _create_session(session_factory, sid, owner)

    with pytest.raises(HTTPException) as exc:
        await mgr.add_message(sid, other, "你谁啊", "拒绝")
    assert exc.value.status_code == 403

    # 消息未写入
    assert await _count_rows(session_factory, ChatMessage, session_id=sid) == 0


# ---------------------------------------------------------------------------
# clear_session
# ---------------------------------------------------------------------------
async def test_clear_session_removes_session_and_messages(mgr, session_factory):
    sid, user = _uid(), _uid("user")
    await _create_session(session_factory, sid, user)
    await _add_messages(session_factory, sid, [("user", "u"), ("assistant", "a")])
    await mgr.add_message(sid, user, "再问", "再答")

    await mgr.clear_session(sid, user)

    assert await _count_rows(session_factory, ChatSession, id=sid) == 0
    assert await _count_rows(session_factory, ChatMessage, session_id=sid) == 0


async def test_clear_session_does_nothing_for_other_users_session(mgr, session_factory):
    sid, owner, other = _uid(), _uid("owner"), _uid("other")
    await _create_session(session_factory, sid, owner)

    await mgr.clear_session(sid, other)  # 不抛错，但也不删除

    assert await _count_rows(session_factory, ChatSession, id=sid) == 1


# ---------------------------------------------------------------------------
# get_all_session_ids / get_user_sessions
# ---------------------------------------------------------------------------
async def test_get_all_session_ids_filters_by_user(mgr, session_factory):
    u1, u2 = _uid("u1"), _uid("u2")
    sids = [_uid(), _uid(), _uid()]
    await _create_session(session_factory, sids[0], u1)
    await _create_session(session_factory, sids[1], u1)
    await _create_session(session_factory, sids[2], u2)

    assert sorted(await mgr.get_all_session_ids()) == sorted(sids)
    assert sorted(await mgr.get_all_session_ids(user_id=u1)) == sorted(sids[:2])
    assert await mgr.get_all_session_ids(user_id=_uid("nobody")) == []


async def test_get_user_sessions_ordered_by_updated_at_desc(mgr, session_factory):
    user = _uid("user")
    now = datetime.now()
    sids = [_uid(), _uid(), _uid()]
    await _create_session(session_factory, sids[0], user, title="最旧")
    await _create_session(session_factory, sids[1], user, title="中间")
    await _create_session(session_factory, sids[2], user, title="最新")

    # 显式错开 updated_at（赋值的值生效，onupdate 不会覆盖显式赋值）
    async with session_factory() as db:
        for sid, ts in zip(sids, [now - timedelta(hours=2), now - timedelta(hours=1), now]):
            session = (await db.execute(select(ChatSession).where(ChatSession.id == sid))).scalar_one()
            session.updated_at = ts
        await db.commit()

    sessions = await mgr.get_user_sessions(user)
    assert [s["id"] for s in sessions] == list(reversed(sids))
    assert sessions[0]["title"] == "最新"
    assert sessions[0]["updated_at"] is not None
    assert sessions[0]["created_at"] is not None