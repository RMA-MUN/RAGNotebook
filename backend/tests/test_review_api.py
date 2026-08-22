"""回顾 API 集成测试（真实 ReviewService + SQLite + 假 LLM）。"""
from datetime import datetime, timedelta

from tests.fakes import TEST_USER_ID


async def _seed_note_with_review(session_factory, note_id="note-1", title="值得回顾的笔记",
                                 due_in_days=-1):
    from app.models.note import Note
    from app.models.review_record import ReviewRecord
    import uuid as uuidlib

    async with session_factory() as s:
        note = Note(id=note_id, user_id=TEST_USER_ID, title=title, content="这是需要复习的内容", category="study")
        s.add(note)
        await s.commit()
        review = ReviewRecord(
            id=str(uuidlib.uuid4()),
            note_id=note_id,
            user_id=TEST_USER_ID,
            review_count=0,
            interval_days=1,
            next_review_at=datetime.now() + timedelta(days=due_in_days),
        )
        s.add(review)
        await s.commit()
        return note, review


async def test_get_today_reviews(client, session_factory):
    await _seed_note_with_review(session_factory, due_in_days=-1)
    await _seed_note_with_review(session_factory, note_id="note-2", title="未到期笔记", due_in_days=10)

    resp = await client.get("/review/today", headers={"Authorization": "Bearer x"})
    body = resp.json()["data"]
    assert body["total_count"] == 1
    assert body["reviews"][0]["note_id"] == "note-1"
    assert body["reviews"][0]["title"] == "值得回顾的笔记"
    assert "内容预览" in str(body["reviews"][0]["content_preview"]) or body["reviews"][0]["content_preview"]


async def test_mark_reviewed(client, session_factory):
    await _seed_note_with_review(session_factory)
    resp = await client.post("/review/done/note-1", headers={"Authorization": "Bearer x"})
    body = resp.json()
    assert body["code"] == 200
    assert "已标记回顾" in body["message"]
    assert body["data"]["review_count"] == 1
    assert body["data"]["interval_days"] == 2  # 艾宾浩斯: 第1次 → 2 天


async def test_mark_reviewed_missing_record(client):
    resp = await client.post("/review/done/no-such-note", headers={"Authorization": "Bearer x"})
    body = resp.json()
    assert body["code"] == 200
    assert "回顾记录不存在" in body["message"]
    assert "data" not in body or body.get("data") is None


async def test_review_question(client, session_factory, monkeypatch):
    from app.core.background_init import init_manager
    from langchain_core.messages import AIMessage

    await _seed_note_with_review(session_factory)

    class CannedChat:
        async def ainvoke(self, messages):
            return AIMessage(content='{"question": "测试问题", "choices": ["A", "B"], "answer": "A"}')

    monkeypatch.setattr(init_manager, "chat_model", CannedChat())

    resp = await client.get("/review/question/note-1", headers={"Authorization": "Bearer x"})
    data = resp.json()["data"]
    assert data["question"] == "测试问题"
    assert data["choices"] == ["A", "B"]
    assert data["answer"] == "A"


async def test_review_question_note_not_found(client):
    resp = await client.get("/review/question/no-such-note", headers={"Authorization": "Bearer x"})
    data = resp.json()["data"]
    assert data["question"] == "笔记不存在"
    assert data["choices"] == []