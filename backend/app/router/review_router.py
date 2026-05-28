"""
回顾提醒 API 路由 —— 今日回顾列表、标记已回顾、获取回顾选择题。
"""
from fastapi import Depends
from fastapi.routing import APIRouter
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.success_response import success_response
from app.db.db_config import get_db
from app.services.review_service import review_service
from app.utils.auth_utils import get_current_user_id

review_router = APIRouter(prefix="/review", tags=["review"])


@review_router.get("/today")
async def get_today_reviews(
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """
    获取今日待回顾的笔记列表。
    返回关联的笔记标题、内容预览、回顾次数、间隔天数等信息。
    """
    reviews = await review_service.get_today_reviews(db, user_id)
    return success_response(data={
        "reviews": reviews,
        "total_count": len(reviews),
    })


@review_router.post("/done/{note_id}")
async def mark_reviewed(
    note_id: str,
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """
    标记笔记已回顾。更新 review_count 并根据艾宾浩斯算法计算下次回顾时间。
    """
    result = await review_service.mark_reviewed(db, note_id, user_id)
    if result["success"]:
        return success_response(message=result["message"], data=result)
    return success_response(message=result["message"])


@review_router.get("/question/{note_id}")
async def get_review_question(
    note_id: str,
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """
    根据笔记内容生成一道回顾选择题。
    返回 {question, choices, answer} 结构。
    """
    question_data = await review_service.get_review_question_for_note(db, note_id, user_id)
    return success_response(data=question_data)
