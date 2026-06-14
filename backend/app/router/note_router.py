"""
笔记管理 API 路由 —— CRUD、搜索、自动标签、内联补全、写作辅助。
"""

from fastapi import Depends, Query
from fastapi.responses import Response, StreamingResponse
from fastapi.routing import APIRouter
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.background_init import init_manager
from app.core.rate_limit import rate_limit
from app.core.success_response import success_response
from app.db.db_config import get_db
from app.schemas.models import (
    BatchCategoryRequest,
    BatchIdsRequest,
    BatchPinRequest,
    NoteCreate,
    NoteListResponse,
    NoteUpdate,
)
from app.utils.auth_utils import get_current_user_id

note_router = APIRouter(prefix="/note", tags=["note"])


async def ensure_note_service():
    """依赖：等待 NoteService 后台初始化完成后再处理请求。"""
    await init_manager.note_service_ready.wait()
    return init_manager.note_service


note_router.dependencies = [Depends(ensure_note_service)]


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
    note = await init_manager.note_service.create_note(db, user_id, payload)
    return success_response(message="笔记创建成功", data=note)


@note_router.get("/list")
async def list_notes(
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    category: str = Query(None),
    tag: str = Query(None),
    sort_by: str = Query("updated_at", pattern="^(updated_at|created_at|title)$"),
):
    """
    笔记列表：分页查询，支持按分类筛选和排序。tag 筛选在内存层完成。
    """
    notes, total = await init_manager.note_service.list_notes(db, user_id, page, page_size, category, tag, sort_by)
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
    notes = await init_manager.note_service.search_notes(db, user_id, q)
    return success_response(data=NoteListResponse(notes=notes, total_count=len(notes)))


@note_router.post("/batch/delete")
async def batch_delete_notes(
    payload: BatchIdsRequest,
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
    _: None = Depends(rate_limit(limit=10, window=60)),
):
    """
    批量删除笔记：按 ID 列表删除笔记及其向量。
    """
    deleted = await init_manager.note_service.batch_delete_notes(db, user_id, payload.ids)
    return success_response(message=f"成功删除 {deleted} 篇笔记")


@note_router.post("/batch/download")
async def batch_download_notes(
    payload: BatchIdsRequest,
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
    _: None = Depends(rate_limit(limit=5, window=60)),
):
    """
    批量下载笔记为 ZIP 压缩包（内含 .md 文件）。
    """
    from urllib.parse import quote
    from datetime import datetime

    zip_bytes = await init_manager.note_service.batch_export_zip(db, user_id, payload.ids)
    date_str = datetime.now().strftime("%Y%m%d")
    filename = f"notes_{date_str}.zip"
    return Response(
        content=zip_bytes,
        media_type="application/zip",
        headers={
            "Content-Disposition": f"attachment; filename=\"{filename}\"; filename*=UTF-8''{quote(filename)}",
        }
    )


@note_router.put("/batch/category")
async def batch_update_category(
    payload: BatchCategoryRequest,
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
    _: None = Depends(rate_limit(limit=10, window=60)),
):
    """
    批量更新笔记分类。
    """
    updated = await init_manager.note_service.batch_update_category(db, user_id, payload.ids, payload.category)
    return success_response(message=f"成功更新 {updated} 篇笔记的分类")


@note_router.put("/batch/pin")
async def batch_pin_notes(
    payload: BatchPinRequest,
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
    _: None = Depends(rate_limit(limit=10, window=60)),
):
    """
    批量置顶/取消置顶笔记。
    """
    updated = await init_manager.note_service.batch_update_pin(db, user_id, payload.ids, payload.is_pinned)
    return success_response(message=f"成功更新 {updated} 篇笔记的置顶状态")


@note_router.get("/stats")
async def get_stats(
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """
    获取用户笔记分类统计。
    返回各分类下的笔记数量及总数。
    """
    stats = await init_manager.note_service.get_category_stats(db, user_id)
    return success_response(data=stats)


@note_router.delete("/category/{category}")
async def delete_category(
    category: str,
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
    _: None = Depends(rate_limit(limit=5, window=60)),
):
    """
    删除某个分类及其下所有笔记。
    返回被删除的笔记数量。
    """
    deleted = await init_manager.note_service.delete_category(db, user_id, category)
    return success_response(data={"deleted_count": deleted}, message=f"成功删除分类「{category}」及其 {deleted} 篇笔记")


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
    result = await init_manager.note_service.autocomplete(payload.context)
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
        init_manager.note_service.assist_stream(payload.content, payload.action),
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
    note = await init_manager.note_service.update_note(db, note_id, user_id, payload)
    if not note:
        return success_response(message="笔记不存在")
    return success_response(message="笔记更新成功", data=note)


@note_router.put("/{note_id}/pin")
async def toggle_pin(
    note_id: str,
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """
    切换笔记置顶状态。
    """
    note = await init_manager.note_service.get_note(db, note_id, user_id)
    if not note:
        return success_response(message="笔记不存在")
    new_pinned = not note.is_pinned
    updated = await init_manager.note_service.update_note(db, note_id, user_id, NoteUpdate(is_pinned=new_pinned))
    return success_response(message="置顶已更新", data=updated)


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
    deleted = await init_manager.note_service.delete_note(db, note_id, user_id)
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
    note = await init_manager.note_service.get_note(db, note_id, user_id)
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
    note = await init_manager.note_service.get_note(db, note_id, user_id)
    if not note:
        return success_response(message="笔记不存在")

    import asyncio
    asyncio.create_task(init_manager.note_service._auto_tag_and_review(note_id, user_id, note.content))
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
    related = await init_manager.note_service.get_related_notes(db, note_id, user_id)
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
    md = await init_manager.note_service.export_note_markdown(db, note_id, user_id)
    if not md:
        return success_response(message="笔记不存在")
    return success_response(data={"markdown": md, "filename": f"{note_id}.md"})


@note_router.get("/{note_id}/download")
async def download_note(
    note_id: str,
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """
    下载笔记为 Markdown 文件（浏览器触发下载）。
    返回 Content-Disposition: attachment 的 markdown 文件响应。
    """
    note = await init_manager.note_service.get_note(db, note_id, user_id)
    if not note:
        return success_response(message="笔记不存在")

    md = await init_manager.note_service.export_note_markdown(db, note_id, user_id)

    import re
    from urllib.parse import quote

    safe_title = re.sub(r'[\\/:*?"<>|]', '_', note.title or note_id)
    filename = f"{safe_title}.md"

    # RFC 5987: filename* 支持 UTF-8 非 ASCII 文件名
    # filename 作为 ASCII-only fallback（避免 latin-1 编码错误）
    return Response(
        content=md.encode("utf-8"),
        media_type="text/markdown; charset=utf-8",
        headers={
            "Content-Disposition": f"attachment; filename=\"{note_id}.md\"; filename*=UTF-8''{quote(filename, safe='')}",
        }
    )
