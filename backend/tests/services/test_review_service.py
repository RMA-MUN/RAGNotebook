"""ReviewService 服务层测试 —— 使用真实逻辑 + SQLite 内存库 + 假 LLM。"""
import json
import uuid
from datetime import datetime, timedelta

import pytest
from sqlalchemy import select

from app.models.note import Note
from app.models.review_record import ReviewRecord
from app.services.review_service import INTERVALS, ReviewService, get_next_interval

from tests.conftest import install_init_manager_fakes
from tests.fakes import make_fake_chat_model

REVIEW_FALLBACK = {
    "question": "请回顾这篇笔记的主要内容",
    "choices": ["不太确定", "需要复习", "基本掌握", "完全理解"],
    "answer": "基本掌握",
}


def _uid(prefix: str = "review") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


async def _seed_note(db, user_id, title="测试笔记", content="这是笔记内容", category="work", tags=None):
    note = Note(
        id=_uid("note"),
        user_id=user_id,
        title=title,
        content=content,
        category=category,
        tags=tags if tags is not None else [],
    )
    db.add(note)
    await db.commit()
    await db.refresh(note)
    return note


async def _seed_review(db, note, user_id, next_review_at, review_count=0, interval_days=1):
    record = ReviewRecord(
        id=_uid("rr"),
        note_id=note.id,
        user_id=user_id,
        review_count=review_count,
        interval_days=interval_days,
        next_review_at=next_review_at,
    )
    db.add(record)
    await db.commit()
    await db.refresh(record)
    return record


def _install_chat(monkeypatch, responses=None):
    """安装假 chat_model 并返回安装后的 init_manager。"""
    return install_init_manager_fakes(monkeypatch, chat_model=make_fake_chat_model(responses=responses))


class _RaisingChatModel:
    """ainvoke 直接抛异常的假模型。"""

    async def ainvoke(self, messages):
        raise RuntimeError("fake model exploded")


# ---------------------------------------------------------------------------
# get_next_interval
# ---------------------------------------------------------------------------
def test_get_next_interval_maps_known_review_counts():
    assert INTERVALS == [1, 2, 4, 7, 15, 30]
    # get_next_interval 是模块级函数（非 ReviewService 方法），按间隔数组映射
    assert [get_next_interval(i) for i in range(6)] == [1, 2, 4, 7, 15, 30]


def test_get_next_interval_caps_at_30():
    for count in (6, 7, 10, 100):
        assert get_next_interval(count) == 30


# ---------------------------------------------------------------------------
# get_today_reviews
# ---------------------------------------------------------------------------
async def test_get_today_reviews_returns_only_due_records(db_session):
    user = _uid("user")
    due_note = await _seed_note(db_session, user, title="到期笔记", content="内容A" * 100)
    future_note = await _seed_note(db_session, user, title="未来笔记")
    record = await _seed_review(db_session, due_note, user, next_review_at=datetime.now() - timedelta(days=1), review_count=1, interval_days=2)
    await _seed_review(db_session, future_note, user, next_review_at=datetime.now() + timedelta(days=10))

    svc = ReviewService()
    reviews = await svc.get_today_reviews(db_session, user)

    assert [r["note_id"] for r in reviews] == [due_note.id]
    item = reviews[0]
    assert item["review_id"] == record.id
    assert item["note_id"] == due_note.id
    assert item["title"] == "到期笔记"
    assert item["content_preview"] == ("内容A" * 100)[:200]
    assert item["tags"] == []
    assert item["category"] == "work"
    assert item["review_count"] == 1
    assert item["interval_days"] == 2
    assert item["last_reviewed_at"] is None


async def test_get_today_reviews_orders_by_next_review_at_asc(db_session):
    user = _uid("user")
    n1 = await _seed_note(db_session, user, title="较早")
    n2 = await _seed_note(db_session, user, title="较晚")
    await _seed_review(db_session, n1, user, next_review_at=datetime.now() - timedelta(days=5))
    await _seed_review(db_session, n2, user, next_review_at=datetime.now() - timedelta(days=1))

    svc = ReviewService()
    reviews = await svc.get_today_reviews(db_session, user)
    assert [r["note_id"] for r in reviews] == [n1.id, n2.id]


async def test_get_today_reviews_empty_for_other_user(db_session):
    user_a = _uid("user-a")
    user_b = _uid("user-b")
    note = await _seed_note(db_session, user_a)
    await _seed_review(db_session, note, user_a, next_review_at=datetime.now() - timedelta(days=1))

    svc = ReviewService()
    assert await svc.get_today_reviews(db_session, user_b) == []


# ---------------------------------------------------------------------------
# mark_reviewed
# ---------------------------------------------------------------------------
async def test_mark_reviewed_missing_record_returns_success_false(db_session):
    svc = ReviewService()
    result = await svc.mark_reviewed(db_session, _uid("nonexistent"), _uid("user"))
    assert result == {"success": False, "message": "回顾记录不存在"}


async def test_mark_reviewed_updates_record_and_commits(db_session, session_factory):
    user = _uid("user")
    note = await _seed_note(db_session, user)
    record = await _seed_review(db_session, note, user, next_review_at=datetime.now() - timedelta(days=1), review_count=0, interval_days=1)

    svc = ReviewService()
    result = await svc.mark_reviewed(db_session, note.id, user)

    assert result["success"] is True
    assert result["review_count"] == 1
    assert result["interval_days"] == 2  # INTERVALS[1]
    assert datetime.fromisoformat(result["next_review_at"]) > datetime.now()

    # 用独立会话验证已提交
    async with session_factory() as fresh:
        row = (await fresh.execute(select(ReviewRecord).where(ReviewRecord.id == record.id))).scalar_one()
        assert row.review_count == 1
        assert row.interval_days == 2
        assert row.last_reviewed_at is not None
        assert row.next_review_at > datetime.now()


async def test_mark_reviewed_tenth_review_caps_interval(db_session):
    user = _uid("user")
    note = await _seed_note(db_session, user)
    await _seed_review(db_session, note, user, next_review_at=datetime.now() - timedelta(days=1), review_count=5, interval_days=30)

    svc = ReviewService()
    result = await svc.mark_reviewed(db_session, note.id, user)
    assert result["success"] is True
    assert result["review_count"] == 6
    assert result["interval_days"] == 30  # 越界后固定 30


# ---------------------------------------------------------------------------
# generate_review_question
# ---------------------------------------------------------------------------
async def test_generate_review_question_success_bare_json(monkeypatch):
    payload = {"question": "什么是间隔重复？", "choices": ["A选项", "B选项", "C选项", "D选项"], "answer": "B选项"}
    _install_chat(monkeypatch, responses=[json.dumps(payload, ensure_ascii=False)])

    svc = ReviewService()
    result = await svc.generate_review_question("笔记内容")
    assert result == payload


async def test_generate_review_question_success_from_markdown_block(monkeypatch):
    payload = {"question": "艾宾浩斯曲线是什么？", "choices": ["a", "b", "c", "d"], "answer": "b"}
    raw = (
        "好的，以下是生成的回顾题：\n"
        "```json\n"
        + json.dumps(payload, ensure_ascii=False)
        + "\n```\n"
        "如有需要可以再调整。"
    )
    _install_chat(monkeypatch, responses=[raw])

    svc = ReviewService()
    result = await svc.generate_review_question("笔记内容")
    assert result == payload


async def test_generate_review_question_success_with_leading_text(monkeypatch):
    payload = {"question": "q?", "choices": ["1", "2", "3", "4"], "answer": "3"}
    # 生产逻辑只从第一个 { 开始截取，不处理 JSON 后的尾随文本
    raw = "这是答案：" + json.dumps(payload, ensure_ascii=False)
    _install_chat(monkeypatch, responses=[raw])

    svc = ReviewService()
    result = await svc.generate_review_question("笔记内容")
    assert result == payload


async def test_generate_review_question_failure_garbage_returns_fallback(monkeypatch, fake_models):
    # fake_models 默认假模型返回非 JSON 文本
    svc = ReviewService()
    result = await svc.generate_review_question("笔记内容")
    assert result == REVIEW_FALLBACK


async def test_generate_review_question_failure_model_raises_returns_fallback(monkeypatch):
    install_init_manager_fakes(monkeypatch, chat_model=_RaisingChatModel())
    svc = ReviewService()
    result = await svc.generate_review_question("笔记内容")
    assert result == REVIEW_FALLBACK


# ---------------------------------------------------------------------------
# get_review_question_for_note
# ---------------------------------------------------------------------------
async def test_get_review_question_for_note_not_found(db_session, fake_models):
    svc = ReviewService()
    result = await svc.get_review_question_for_note(db_session, _uid("missing"), _uid("user"))
    assert result == {"question": "笔记不存在", "choices": [], "answer": ""}


async def test_get_review_question_for_note_other_users_note(db_session, fake_models):
    owner = _uid("owner")
    other = _uid("other")
    note = await _seed_note(db_session, owner)

    svc = ReviewService()
    result = await svc.get_review_question_for_note(db_session, note.id, other)
    assert result == {"question": "笔记不存在", "choices": [], "answer": ""}


async def test_get_review_question_for_note_found_generates_question(db_session, monkeypatch):
    user = _uid("user")
    note = await _seed_note(db_session, user, content="基于向量检索的 RAG 总结")
    payload = {"question": "RAG 是什么？", "choices": ["x", "y", "z", "w"], "answer": "y"}
    _install_chat(monkeypatch, responses=[json.dumps(payload, ensure_ascii=False)])

    svc = ReviewService()
    result = await svc.get_review_question_for_note(db_session, note.id, user)
    assert result == payload