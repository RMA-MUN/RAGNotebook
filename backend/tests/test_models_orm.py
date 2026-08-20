"""ORM 模型测试：默认值 / 主键 / Base.metadata 注册 / 关键列约束。"""
import re

import pytest
from sqlalchemy.exc import IntegrityError

from app.models.chat_history import Base, ChatMessage, ChatSession
from app.models.note import Note
from app.models.note_template import NoteTemplate
from app.models.review_record import ReviewRecord
from app.models.user_model import User, UserStatusChoice, generate_uuid


HEX24 = re.compile(r"^[0-9a-f]{24}$")


class TestGenerateUuid:
    def test_returns_24_char_hex(self):
        value = generate_uuid()
        assert HEX24.match(value)

    def test_values_are_unique(self):
        values = {generate_uuid() for _ in range(100)}
        assert len(values) == 100


class TestMetadataRegistration:
    def test_all_tables_registered(self):
        tables = set(Base.metadata.tables.keys())
        assert {
            "user_service",
            "chat_sessions",
            "chat_messages",
            "notes",
            "note_templates",
            "review_records",
        } <= tables

    def test_expected_table_names(self):
        assert User.__tablename__ == "user_service"
        assert ChatSession.__tablename__ == "chat_sessions"
        assert ChatMessage.__tablename__ == "chat_messages"
        assert Note.__tablename__ == "notes"
        assert NoteTemplate.__tablename__ == "note_templates"
        assert ReviewRecord.__tablename__ == "review_records"

    def test_primary_keys(self):
        assert User.__table__.c.uuid.primary_key
        assert ChatSession.__table__.c.id.primary_key
        assert ChatMessage.__table__.c.id.primary_key
        assert Note.__table__.c.id.primary_key
        assert NoteTemplate.__table__.c.id.primary_key
        assert ReviewRecord.__table__.c.id.primary_key

    def test_required_columns_are_not_nullable(self):
        assert User.__table__.c.username.nullable is False
        assert User.__table__.c.email.nullable is False
        assert ChatMessage.__table__.c.role.nullable is False
        assert ChatMessage.__table__.c.content.nullable is False
        assert Note.__table__.c.title.nullable is False
        assert Note.__table__.c.content.nullable is False
        assert NoteTemplate.__table__.c.name.nullable is False
        assert ReviewRecord.__table__.c.note_id.nullable is False


class TestUserModel:
    async def test_default_uuid_generated_on_flush(self, db_session):
        user = User(username="alice", email="alice@example.com", password="secret123")
        assert user.uuid is None  # Python 侧默认值在 flush 时才生效

        db_session.add(user)
        await db_session.flush()

        assert user.uuid is not None
        assert HEX24.match(user.uuid)

    async def test_uuids_unique_across_users(self, db_session):
        from sqlalchemy import select

        db_session.add_all(
            [
                User(username="alice", email="alice@example.com", password="secret123"),
                User(username="bob", email="bob@example.com", password="secret123"),
            ]
        )
        await db_session.flush()
        users = (await db_session.execute(select(User).order_by(User.username))).scalars().all()
        u1, u2 = users[0].uuid, users[1].uuid
        assert u1 != u2
        assert HEX24.match(u1) and HEX24.match(u2)

    async def test_defaults_applied(self, db_session):
        user = User(username="alice", email="alice@example.com", password="secret123")
        db_session.add(user)
        await db_session.flush()

        assert user.is_active is False
        assert user.status == UserStatusChoice.DISABLED
        assert user.date_joined is not None  # server_default now()

    async def test_email_unique_constraint(self, db_session):
        db_session.add(User(username="alice", email="same@example.com", password="secret123"))
        await db_session.flush()
        db_session.add(User(username="bob", email="same@example.com", password="secret123"))
        with pytest.raises(IntegrityError):
            await db_session.flush()

    async def test_username_required(self, db_session):
        db_session.add(User(email="x@example.com", password="secret123"))
        with pytest.raises(IntegrityError):
            await db_session.flush()


class TestChatModels:
    async def test_chat_session_default_title_and_metadata(self, db_session):
        session = ChatSession(id="sess-1", user_id="u1", metadata_={"lang": "zh"})
        db_session.add(session)
        await db_session.flush()

        assert session.id == "sess-1"
        assert session.title == "新的对话"
        assert session.metadata_ == {"lang": "zh"}
        assert session.created_at is not None

    async def test_chat_message_autoincrement_pk(self, db_session):
        session = ChatSession(id="sess-2", user_id="u1")
        db_session.add(session)
        await db_session.flush()

        message = ChatMessage(session_id="sess-2", role="user", content="你好", metadata_={"a": 1})
        db_session.add(message)
        await db_session.flush()

        assert isinstance(message.id, int)
        assert message.id > 0
        assert message.content == "你好"
        assert message.metadata_ == {"a": 1}

    async def test_chat_message_role_required(self, db_session):
        db_session.add(ChatMessage(session_id="sess-1", content="x"))
        with pytest.raises(IntegrityError):
            await db_session.flush()


class TestNoteModel:
    async def test_defaults_applied(self, db_session):
        note = Note(id="note-1", user_id="u1", title="标题", content="内容")
        db_session.add(note)
        await db_session.flush()

        assert note.is_pinned is False
        assert note.tags is None
        assert note.created_at is not None

    async def test_json_tags_round_trip(self, db_session):
        note = Note(id="note-2", user_id="u1", title="t", content="c", tags=["AI", "FastAPI"])
        db_session.add(note)
        await db_session.flush()
        assert note.tags == ["AI", "FastAPI"]

    async def test_title_required(self, db_session):
        db_session.add(Note(id="note-3", user_id="u1", content="c"))
        with pytest.raises(IntegrityError):
            await db_session.flush()


class TestNoteTemplateModel:
    async def test_defaults_applied(self, db_session):
        template = NoteTemplate(id="tpl-1", user_id="u1", name="模板")
        db_session.add(template)
        await db_session.flush()

        assert template.icon == "FileText"
        assert template.category == ""
        assert template.title == ""
        assert template.content == ""
        assert template.tags == []
        assert template.is_default is False
        assert template.sort_order == 0

    async def test_json_tags_default_empty_list(self, db_session):
        template = NoteTemplate(id="tpl-2", user_id="u1", name="模板", tags=["A"])
        db_session.add(template)
        await db_session.flush()
        assert template.tags == ["A"]

    async def test_name_required(self, db_session):
        db_session.add(NoteTemplate(id="tpl-3", user_id="u1"))
        with pytest.raises(IntegrityError):
            await db_session.flush()


class TestReviewRecordModel:
    async def test_defaults_applied(self, db_session):
        note = Note(id="note-r", user_id="u1", title="t", content="c")
        db_session.add(note)
        await db_session.flush()

        record = ReviewRecord(id="rec-1", note_id=note.id, user_id="u1")
        db_session.add(record)
        await db_session.flush()

        assert record.review_count == 0
        assert record.interval_days == 1
        assert record.last_reviewed_at is None
        assert record.next_review_at is None

    async def test_foreign_key_enforced(self, db_session):
        # 指向不存在的 note 应报完整性错误（db_engine 已开启 PRAGMA foreign_keys）
        db_session.add(ReviewRecord(id="rec-2", note_id="missing-note", user_id="u1"))
        with pytest.raises(IntegrityError):
            await db_session.flush()