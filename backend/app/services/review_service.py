"""
回顾服务层 —— 艾宾浩斯间隔重复算法 + 回顾问题生成。
"""
import json
from datetime import datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logger_handler import logger
from app.models.note import Note
from app.models.review_record import ReviewRecord

# 艾宾浩斯间隔重复数组（天）
INTERVALS = [1, 2, 4, 7, 15, 30]


def get_next_interval(review_count: int) -> int:
    """
    根据回顾次数返回下一次回顾间隔天数。
    超出预定义数组后固定使用 30 天间隔。
    """
    if review_count < len(INTERVALS):
        return INTERVALS[review_count]
    return INTERVALS[-1]


class ReviewService:
    """
    回顾服务 —— 负责今日回顾查询、标记已回顾、生成 LLM 回顾问题。
    """

    async def get_today_reviews(self, db: AsyncSession, user_id: str) -> list[dict]:
        """
        查询今日待回顾的笔记列表（next_review_at <= 当前时间）。
        同时关联查询笔记内容用于生成回顾问题。
        """
        now = datetime.now()

        # 查询待回顾的记录，关联笔记表获取标题和内容
        stmt = (
            select(ReviewRecord, Note.title, Note.content, Note.tags, Note.category)
            .join(Note, ReviewRecord.note_id == Note.id)
            .where(
                ReviewRecord.user_id == user_id,
                ReviewRecord.next_review_at <= now,
            )
            .order_by(ReviewRecord.next_review_at.asc())
        )
        result = await db.execute(stmt)
        rows = result.all()

        reviews = []
        for record, title, content, tags, category in rows:
            reviews.append({
                "review_id": record.id,
                "note_id": record.note_id,
                "title": title,
                "content_preview": content[:200] if content else "",
                "tags": tags,
                "category": category,
                "review_count": record.review_count,
                "last_reviewed_at": str(record.last_reviewed_at) if record.last_reviewed_at else None,
                "interval_days": record.interval_days,
            })

        return reviews

    async def mark_reviewed(self, db: AsyncSession, note_id: str, user_id: str) -> dict:
        """
        标记笔记已回顾，更新 review_count 和 next_review_at。
        返回最新的间隔信息。
        """
        # 查询当前记录
        stmt = select(ReviewRecord).where(
            ReviewRecord.note_id == note_id,
            ReviewRecord.user_id == user_id,
        )
        result = await db.execute(stmt)
        record = result.scalar_one_or_none()

        if not record:
            return {"success": False, "message": "回顾记录不存在"}

        now = datetime.now()
        new_count = record.review_count + 1
        next_interval = get_next_interval(new_count)
        next_at = now + timedelta(days=next_interval)

        stmt = (
            update(ReviewRecord)
            .where(ReviewRecord.id == record.id)
            .values(
                review_count=new_count,
                interval_days=next_interval,
                last_reviewed_at=now,
                next_review_at=next_at,
            )
        )
        await db.execute(stmt)
        await db.commit()

        logger.info(f"标记回顾完成 note_id={note_id}, 第{new_count}次回顾, 下次间隔{next_interval}天")

        return {
            "success": True,
            "message": "已标记回顾",
            "review_count": new_count,
            "interval_days": next_interval,
            "next_review_at": str(next_at),
        }

    async def generate_review_question(self, content: str) -> dict:
        """
        调用 LLM 根据笔记内容生成回顾选择题。
        返回 {question, choices, answer} 结构。
        """
        raw = ""
        try:
            from langchain_core.messages import HumanMessage

            from app.core.background_init import init_manager
            from app.utils.prompt_loader import load_prompt

            chat_model = init_manager.chat_model
            prompt_template = load_prompt("review_question_prompt")
            prompt = prompt_template.format(content=content[:2000])
            response = await chat_model.ainvoke([HumanMessage(content=prompt)])
            raw = response.content.strip()
            logger.debug(f"LLM 原始响应: {raw[:500]}")

            # 尝试从 markdown 代码块中提取 JSON
            if "```json" in raw:
                raw = raw.split("```json")[1].split("```")[0].strip()
            elif "```" in raw:
                raw = raw.split("```")[1].split("```")[0].strip()
            # 如果还有前置文本，找到第一个 { 开始解析
            brace_start = raw.find("{")
            if brace_start > 0:
                raw = raw[brace_start:]
            data = json.loads(raw)
            logger.debug(f"解析后的JSON: {data}")

            return {
                "question": data["question"],
                "choices": data["choices"],
                "answer": data["answer"],
            }
        except Exception as e:
            logger.error(f"生成回顾问题失败: {e} | raw={raw[:300]}")
            return {
                "question": "请回顾这篇笔记的主要内容",
                "choices": ["不太确定", "需要复习", "基本掌握", "完全理解"],
                "answer": "基本掌握",
            }

    async def get_review_question_for_note(
        self, db: AsyncSession, note_id: str, user_id: str
    ) -> dict:
        """
        根据 note_id 查询笔记内容，生成回顾选择题。
        同时校验笔记归属。
        """
        stmt = select(Note).where(Note.id == note_id, Note.user_id == user_id)
        result = await db.execute(stmt)
        note = result.scalar_one_or_none()
        if not note:
            return {
                "question": "笔记不存在",
                "choices": [],
                "answer": "",
            }
        return await self.generate_review_question(note.content or "")


review_service = ReviewService()


def get_review_service() -> ReviewService:
    """依赖注入工厂函数。"""
    return review_service
