"""
笔记模板服务 —— CRUD + 内置模板初始化。
"""

import uuid
import logging

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.note_template import NoteTemplate
from app.schemas.models import (
    NoteTemplateCreate,
    NoteTemplateReorder,
    NoteTemplateResponse,
    NoteTemplateUpdate,
)

logger = logging.getLogger(__name__)

DEFAULT_TEMPLATES = [
    {
        "name": "空白笔记",
        "icon": "FileText",
        "category": "",
        "title": "",
        "content": "",
        "tags": [],
    },
    {
        "name": "会议纪要",
        "icon": "Users",
        "category": "work",
        "title": "会议纪要 - ",
        "content": "## 会议信息\n- **时间**：\n- **参与人**：\n- **主题**：\n\n## 会议内容\n\n\n## 待办事项\n- [ ] \n",
        "tags": ["会议"],
    },
    {
        "name": "学习笔记",
        "icon": "GraduationCap",
        "category": "study",
        "title": "",
        "content": "## 学习目标\n\n\n## 核心内容\n\n\n## 总结与反思\n\n",
        "tags": ["学习"],
    },
    {
        "name": "日记",
        "icon": "BookOpen",
        "category": "life",
        "title": "",
        "content": "## 今日记录\n\n\n## 心情\n\n\n## 明日计划\n- [ ] \n",
        "tags": ["日记"],
    },
    {
        "name": "项目计划",
        "icon": "ListTodo",
        "category": "project",
        "title": "",
        "content": "## 项目概述\n\n\n## 目标\n- [ ] \n\n## 里程碑\n| 阶段 | 内容 | 截止日期 | 状态 |\n|------|------|----------|------|\n| 1    |      |          | 待开始 |\n\n## 备注\n\n",
        "tags": ["项目"],
    },
    {
        "name": "读书笔记",
        "icon": "BookMarked",
        "category": "study",
        "title": "",
        "content": "## 书籍信息\n- **书名**：\n- **作者**：\n\n## 核心观点\n\n\n## 精彩摘录\n\n\n## 读后感\n\n",
        "tags": ["读书"],
    },
]


class NoteTemplateService:

    def _to_response(self, t: NoteTemplate) -> NoteTemplateResponse:
        return NoteTemplateResponse(
            id=t.id,
            user_id=t.user_id,
            name=t.name,
            icon=t.icon,
            category=t.category or "",
            title=t.title or "",
            content=t.content or "",
            tags=t.tags if t.tags else [],
            is_default=t.is_default,
            sort_order=t.sort_order or 0,
            created_at=str(t.created_at) if t.created_at else None,
            updated_at=str(t.updated_at) if t.updated_at else None,
        )

    async def _seed_defaults(self, db: AsyncSession, user_id: str):
        """为新用户创建内置模板。"""
        for i, tpl in enumerate(DEFAULT_TEMPLATES):
            db.add(NoteTemplate(
                id=str(uuid.uuid4()),
                user_id=user_id,
                name=tpl["name"],
                icon=tpl["icon"],
                category=tpl["category"],
                title=tpl["title"],
                content=tpl["content"],
                tags=tpl["tags"],
                is_default=True,
                sort_order=i,
            ))
        await db.commit()

    async def list_templates(self, db: AsyncSession, user_id: str) -> list[NoteTemplateResponse]:
        """列出用户所有模板，首次调用时自动创建内置模板。"""
        count_stmt = select(func.count(NoteTemplate.id)).where(NoteTemplate.user_id == user_id)
        result = await db.execute(count_stmt)
        count = result.scalar() or 0

        if count == 0:
            await self._seed_defaults(db, user_id)

        stmt = select(NoteTemplate).where(NoteTemplate.user_id == user_id).order_by(NoteTemplate.sort_order.asc(), NoteTemplate.created_at.asc())
        result = await db.execute(stmt)
        return [self._to_response(t) for t in result.scalars().all()]

    async def create_template(self, db: AsyncSession, user_id: str, payload: NoteTemplateCreate) -> NoteTemplateResponse:
        """创建自定义模板。"""
        max_order_stmt = select(func.coalesce(func.max(NoteTemplate.sort_order), -1)).where(NoteTemplate.user_id == user_id)
        result = await db.execute(max_order_stmt)
        max_order = result.scalar() or -1

        template = NoteTemplate(
            id=str(uuid.uuid4()),
            user_id=user_id,
            name=payload.name,
            icon=payload.icon,
            category=payload.category,
            title=payload.title,
            content=payload.content,
            tags=payload.tags,
            is_default=False,
            sort_order=max_order + 1,
        )
        db.add(template)
        await db.commit()
        await db.refresh(template)
        return self._to_response(template)

    async def update_template(self, db: AsyncSession, template_id: str, user_id: str, payload: NoteTemplateUpdate) -> NoteTemplateResponse | None:
        """更新模板（包括内置模板）。"""
        stmt = select(NoteTemplate).where(NoteTemplate.id == template_id, NoteTemplate.user_id == user_id)
        result = await db.execute(stmt)
        template = result.scalar_one_or_none()
        if not template:
            return None

        if payload.name is not None:
            template.name = payload.name
        if payload.icon is not None:
            template.icon = payload.icon
        if payload.category is not None:
            template.category = payload.category
        if payload.title is not None:
            template.title = payload.title
        if payload.content is not None:
            template.content = payload.content
        if payload.tags is not None:
            template.tags = payload.tags

        await db.commit()
        await db.refresh(template)
        return self._to_response(template)

    async def delete_template(self, db: AsyncSession, template_id: str, user_id: str) -> bool:
        """删除自定义模板（内置模板不可删除）。"""
        stmt = select(NoteTemplate).where(NoteTemplate.id == template_id, NoteTemplate.user_id == user_id)
        result = await db.execute(stmt)
        template = result.scalar_one_or_none()
        if not template or template.is_default:
            return False

        await db.delete(template)
        await db.commit()
        return True

    async def reorder_templates(self, db: AsyncSession, user_id: str, payload: NoteTemplateReorder) -> bool:
        """重新排序模板。"""
        stmt = select(NoteTemplate).where(
            NoteTemplate.user_id == user_id,
            NoteTemplate.id.in_(payload.ids),
        )
        result = await db.execute(stmt)
        templates = {t.id: t for t in result.scalars().all()}

        if len(templates) != len(payload.ids):
            return False

        for idx, tid in enumerate(payload.ids):
            if tid in templates:
                templates[tid].sort_order = idx

        await db.commit()
        return True


_note_template_service: NoteTemplateService | None = None


def get_note_template_service() -> NoteTemplateService:
    global _note_template_service
    if _note_template_service is None:
        _note_template_service = NoteTemplateService()
    return _note_template_service
