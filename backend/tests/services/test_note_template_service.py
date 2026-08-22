"""NoteTemplateService 服务层测试 —— 真实逻辑 + SQLite 内存库。"""
import uuid

from sqlalchemy import func, select

from app.models.note_template import NoteTemplate
from app.schemas.models import NoteTemplateCreate, NoteTemplateReorder, NoteTemplateUpdate
from app.services.note_template_service import DEFAULT_TEMPLATES, NoteTemplateService


def _uid(prefix: str = "tpl") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


async def _count_templates(db, user_id: str) -> int:
    result = await db.execute(select(func.count(NoteTemplate.id)).where(NoteTemplate.user_id == user_id))
    return result.scalar() or 0


# ---------------------------------------------------------------------------
# list_templates（首次调用自动播种）
# ---------------------------------------------------------------------------
async def test_list_templates_seeds_six_defaults_on_first_call(db_session):
    user = _uid()
    svc = NoteTemplateService()

    templates = await svc.list_templates(db_session, user)

    assert len(templates) == 6
    assert [t.name for t in templates] == [d["name"] for d in DEFAULT_TEMPLATES]
    assert all(t.is_default for t in templates)
    assert [t.sort_order for t in templates] == list(range(6))
    assert all(t.user_id == user for t in templates)


async def test_list_templates_does_not_reseed_on_second_call(db_session):
    user = _uid()
    svc = NoteTemplateService()
    await svc.list_templates(db_session, user)

    again = await svc.list_templates(db_session, user)
    assert len(again) == 6
    assert await _count_templates(db_session, user) == 6


async def test_list_templates_seeds_per_user(db_session):
    user_a, user_b = _uid("a"), _uid("b")
    svc = NoteTemplateService()
    await svc.list_templates(db_session, user_a)

    assert len(await svc.list_templates(db_session, user_b)) == 6
    assert await _count_templates(db_session, user_a) == 6
    assert await _count_templates(db_session, user_b) == 6


# ---------------------------------------------------------------------------
# create_template
# ---------------------------------------------------------------------------
async def test_create_template_after_seed_uses_max_plus_one(db_session):
    user = _uid()
    svc = NoteTemplateService()
    await svc.list_templates(db_session, user)

    created = await svc.create_template(
        db_session, user, NoteTemplateCreate(name="我的模板", icon="Star", category="work", title="标题", content="# 内容", tags=["a"])
    )
    assert created.sort_order == 6
    assert created.is_default is False
    assert created.name == "我的模板"
    assert created.tags == ["a"]


async def test_create_template_on_empty_user_starts_at_zero(db_session):
    user = _uid()
    svc = NoteTemplateService()

    created = await svc.create_template(db_session, user, NoteTemplateCreate(name="首个模板"))
    assert created.sort_order == 0
    assert created.is_default is False
    assert [t.sort_order for t in await svc.list_templates(db_session, user)] == [0]


# ---------------------------------------------------------------------------
# update_template
# ---------------------------------------------------------------------------
async def test_update_template_not_found_returns_none(db_session):
    user = _uid()
    svc = NoteTemplateService()
    result = await svc.update_template(db_session, _uid("missing"), user, NoteTemplateUpdate(name="x"))
    assert result is None


async def test_update_template_updates_fields(db_session):
    user = _uid()
    svc = NoteTemplateService()
    [default] = [t for t in await svc.list_templates(db_session, user) if t.name == "空白笔记"]

    updated = await svc.update_template(
        db_session, default.id, user,
        NoteTemplateUpdate(name="新名字", category="life", tags=["新标签"]),
    )
    assert updated.name == "新名字"
    assert updated.category == "life"
    assert updated.tags == ["新标签"]
    # 未提供的字段保持不变
    assert updated.icon == "FileText"


# ---------------------------------------------------------------------------
# delete_template
# ---------------------------------------------------------------------------
async def test_delete_template_missing_returns_false(db_session):
    user = _uid()
    svc = NoteTemplateService()
    assert await svc.delete_template(db_session, _uid("missing"), user) is False


async def test_delete_template_default_returns_false(db_session):
    user = _uid()
    svc = NoteTemplateService()
    [default] = [t for t in await svc.list_templates(db_session, user) if t.name == "日记"]

    assert await svc.delete_template(db_session, default.id, user) is False
    assert await _count_templates(db_session, user) == 6


async def test_delete_template_custom_returns_true_and_removes(db_session):
    user = _uid()
    svc = NoteTemplateService()
    await svc.list_templates(db_session, user)  # 先播种 6 个内置模板
    created = await svc.create_template(db_session, user, NoteTemplateCreate(name="临时模板"))

    assert await svc.delete_template(db_session, created.id, user) is True
    assert await _count_templates(db_session, user) == 6  # 只剩 6 个内置


# ---------------------------------------------------------------------------
# reorder_templates
# ---------------------------------------------------------------------------
async def test_reorder_templates_mismatched_ids_returns_false(db_session):
    user = _uid()
    svc = NoteTemplateService()
    templates = await svc.list_templates(db_session, user)

    # 传入一个不存在的 id → 匹配数量不符
    bad_ids = [t.id for t in templates[:2]] + [_uid("ghost")]
    assert await svc.reorder_templates(db_session, user, NoteTemplateReorder(ids=bad_ids)) is False


async def test_reorder_templates_other_users_ids_returns_false(db_session):
    user_a, user_b = _uid("a"), _uid("b")
    svc = NoteTemplateService()
    b_templates = await svc.list_templates(db_session, user_b)

    assert await svc.reorder_templates(db_session, user_a, NoteTemplateReorder(ids=[t.id for t in b_templates])) is False


async def test_reorder_templates_success_updates_sort_order(db_session):
    user = _uid()
    svc = NoteTemplateService()
    templates = await svc.list_templates(db_session, user)
    ids = [t.id for t in templates]
    reversed_ids = list(reversed(ids))

    assert await svc.reorder_templates(db_session, user, NoteTemplateReorder(ids=reversed_ids)) is True

    after = await svc.list_templates(db_session, user)
    assert [t.id for t in after] == reversed_ids
    assert [t.sort_order for t in after] == list(range(6))