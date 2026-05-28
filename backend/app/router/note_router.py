"""
笔记管理 API 路由 —— CRUD、搜索、自动标签、内联补全、写作辅助。
"""

from fastapi import Depends, Query
from fastapi.responses import StreamingResponse
from fastapi.routing import APIRouter
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.rate_limit import rate_limit
from app.core.success_response import success_response
from app.db.db_config import get_db
from app.schemas.models import (
    NoteCreate,
    NoteListResponse,
    NoteUpdate,
)
from app.services.note_service import note_service
from app.utils.auth_utils import get_current_user_id

note_router = APIRouter(prefix="/note", tags=["note"])


@note_router.post("/create")
async def create_note(
    payload: NoteCreate,
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
    _: None = Depends(rate_limit(limit=10, window=60)),
):
    """
    创建笔记：
    1. MySQL 写入 + ChromaDB 向量化
    2. 立即返回笔记（tags/category 初始为空）
    3. 后台异步生成标签和回顾记录
    """
    note = await note_service.create_note(db, user_id, payload)
    return success_response(message="笔记创建成功", data=note)


@note_router.get("/list")
async def list_notes(
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    category: str = Query(None),
    tag: str = Query(None),
):
    """
    笔记列表：分页查询，支持按分类筛选。tag 筛选在内存层完成。
    """
    notes, total = await note_service.list_notes(db, user_id, page, page_size, category, tag)
    return success_response(data=NoteListResponse(notes=notes, total_count=total))


@note_router.get("/search")
async def search_notes(
    q: str = Query(..., description="搜索关键词"),
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """
    全文语义搜索：走 ChromaDB notes_collection 向量检索，
    返回当前用户的语义相似笔记。
    """
    notes = await note_service.search_notes(db, user_id, q)
    return success_response(data=NoteListResponse(notes=notes, total_count=len(notes)))


@note_router.get("/stats")
async def get_stats(
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """
    获取用户笔记分类统计。
    返回各分类下的笔记数量及总数。
    """
    stats = await note_service.get_category_stats(db, user_id)
    return success_response(data=stats)


class AutocompleteRequest(BaseModel):
    """内联补全请求模型"""
    context: str


@note_router.post("/autocomplete")
async def autocomplete(
    payload: AutocompleteRequest,
    user_id: str = Depends(get_current_user_id),
):
    """
    AI 内联补全。基于光标前上下文，调用本地 Ollama qwen3:0.8b 快速返回续写文本。
    非流式，目标延迟 300-500ms。
    """
    result = await note_service.autocomplete(payload.context)
    return success_response(data=result)


class AssistRequest(BaseModel):
    """写作辅助请求模型"""
    content: str
    action: str = "continue"


@note_router.post("/assist/stream")
async def assist_stream(
    payload: AssistRequest,
    user_id: str = Depends(get_current_user_id),
    _: None = Depends(rate_limit(limit=10, window=60)),
):
    """
    AI 写作辅助 SSE 流式输出。支持三种模式：
    - continue：续写
    - expand：扩写
    - summarize：缩写
    """
    return StreamingResponse(
        note_service.assist_stream(payload.content, payload.action),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        }
    )


@note_router.put("/{note_id}")
async def update_note(
    note_id: str,
    payload: NoteUpdate,
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
    _: None = Depends(rate_limit(limit=10, window=60)),
):
    """
    更新笔记：修改 title/content，content 变更时同步更新 ChromaDB 向量。
    """
    note = await note_service.update_note(db, note_id, user_id, payload)
    if not note:
        return success_response(message="笔记不存在")
    return success_response(message="笔记更新成功", data=note)


@note_router.delete("/{note_id}")
async def delete_note(
    note_id: str,
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
    _: None = Depends(rate_limit(limit=10, window=60)),
):
    """
    删除笔记：联删 MySQL 记录、ChromaDB 向量、以及级联的 review_records。
    """
    deleted = await note_service.delete_note(db, note_id, user_id)
    if not deleted:
        return success_response(message="笔记不存在")
    return success_response(message="笔记删除成功")


@note_router.get("/{note_id}")
async def get_note(
    note_id: str,
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """
    获取笔记详情。
    """
    note = await note_service.get_note(db, note_id, user_id)
    if not note:
        return success_response(message="笔记不存在")
    return success_response(data=note)


@note_router.post("/{note_id}/auto-tag")
async def regenerate_tags(
    note_id: str,
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """
    手动触发重新生成标签。
    """
    note = await note_service.get_note(db, note_id, user_id)
    if not note:
        return success_response(message="笔记不存在")

    import asyncio
    asyncio.create_task(note_service._auto_tag_and_review(note_id, user_id, note.content))
    return success_response(message="标签生成任务已提交")


@note_router.get("/{note_id}/related")
async def get_related_notes(
    note_id: str,
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """
    获取当前笔记的语义相似笔记和知识库文档（Top 3），
    标注来源：note（笔记库）或 knowledge_base（知识库）。
    """
    related = await note_service.get_related_notes(db, note_id, user_id)
    return success_response(data=related)


@note_router.get("/{note_id}/export")
async def export_note(
    note_id: str,
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """
    导出单篇笔记为 Markdown 格式纯文本。
    """
    md = await note_service.export_note_markdown(db, note_id, user_id)
    if not md:
        return success_response(message="笔记不存在")
    return success_response(data={"markdown": md, "filename": f"{note_id}.md"})
