"""
笔记模板 API 路由 —— CRUD。
"""

from fastapi import Depends
from fastapi.routing import APIRouter
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.rate_limit import rate_limit
from app.core.success_response import success_response
from app.db.db_config import get_db
from app.schemas.models import (
    NoteTemplateCreate,
    NoteTemplateReorder,
    NoteTemplateUpdate,
)
from app.services.note_template_service import get_note_template_service
from app.utils.auth_utils import get_current_user_id

note_template_router = APIRouter(prefix="/note-template", tags=["note-template"])


@note_template_router.get("/list")
async def list_templates(
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """列出当前用户的所有模板。"""
    svc = get_note_template_service()
    templates = await svc.list_templates(db, user_id)
    return success_response(data=templates)


@note_template_router.post("/create")
async def create_template(
    payload: NoteTemplateCreate,
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
    _: None = Depends(rate_limit(limit=20, window=60)),
):
    """创建自定义模板。"""
    svc = get_note_template_service()
    template = await svc.create_template(db, user_id, payload)
    return success_response(message="模板创建成功", data=template)


@note_template_router.put("/{template_id}")
async def update_template(
    template_id: str,
    payload: NoteTemplateUpdate,
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
    _: None = Depends(rate_limit(limit=20, window=60)),
):
    """更新自定义模板。"""
    svc = get_note_template_service()
    template = await svc.update_template(db, template_id, user_id, payload)
    if not template:
        return success_response(message="模板不存在或为内置模板")
    return success_response(message="模板更新成功", data=template)


@note_template_router.delete("/{template_id}")
async def delete_template(
    template_id: str,
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
    _: None = Depends(rate_limit(limit=20, window=60)),
):
    """删除自定义模板。"""
    svc = get_note_template_service()
    deleted = await svc.delete_template(db, template_id, user_id)
    if not deleted:
        return success_response(message="模板不存在或为内置模板")
    return success_response(message="模板删除成功")


@note_template_router.put("/reorder")
async def reorder_templates(
    payload: NoteTemplateReorder,
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """重新排序模板。"""
    svc = get_note_template_service()
    ok = await svc.reorder_templates(db, user_id, payload)
    if not ok:
        return success_response(message="排序失败，模板ID不匹配")
    return success_response(message="排序成功")
