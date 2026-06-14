"""
笔记服务层 —— 包含 CRUD、向量双写、异步自动标签等核心业务逻辑。
"""
import asyncio
import io
import json
import re
import uuid
import zipfile
from datetime import datetime, timedelta

from langchain_chroma import Chroma
from langchain_core.documents import Document
from langchain_core.messages import HumanMessage
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logger_handler import logger
from app.models.note import Note
from app.models.review_record import ReviewRecord
from app.schemas.models import NoteCreate, NoteResponse, NoteUpdate
from app.utils.config import chroma_config
from app.utils.path_tool import get_abstract_path
from app.utils.prompt_loader import load_prompt

NOTES_COLLECTION_NAME = "notes_collection"

# 艾宾浩斯间隔重复数组（天）
INTERVALS = [1, 2, 4, 7, 15, 30]


def _get_next_interval(review_count: int) -> int:
    """
    根据回顾次数返回下一次回顾间隔天数。
    超出预定义数组后固定使用 30 天间隔。
    """
    if review_count < len(INTERVALS):
        return INTERVALS[review_count]
    return INTERVALS[-1]


class NoteService:
    """
    笔记服务 —— 单例模式（模块级实例 note_service）。

    职责：
    - 笔记 CRUD（MySQL 存储）
    - 向量双写（ChromaDB notes_collection）
    - 异步自动标签生成（LLM 后台任务）
    """

    def __init__(self, embed_model=None):
        """
        初始化 ChromaDB 笔记集合。复用现有 persist_directory 但使用独立 collection。
        :param embed_model: 嵌入模型实例（后台初始化完成后传入）
        """
        persist_dir = get_abstract_path(chroma_config['persist_directory'])
        self._notes_store = Chroma(
            collection_name=NOTES_COLLECTION_NAME,
            embedding_function=embed_model,
            persist_directory=persist_dir,
        )

    @property
    def notes_store(self):
        return self._notes_store

    def _doc_to_response(self, note: Note) -> NoteResponse:
        """
        将 SQLAlchemy ORM 对象转换为 Pydantic 响应模型。
        """
        return NoteResponse(
            id=note.id,
            user_id=note.user_id,
            title=note.title,
            content=note.content,
            tags=note.tags if note.tags else None,
            category=note.category,
            is_pinned=note.is_pinned if note.is_pinned else False,
            created_at=str(note.created_at) if note.created_at else None,
            updated_at=str(note.updated_at) if note.updated_at else None,
        )

    async def create_note(self, db: AsyncSession, user_id: str, payload: NoteCreate) -> NoteResponse:
        """
        创建笔记：
        1. MySQL 写入笔记（若用户提供了 tags/category 直接写入）
        2. ChromaDB 写入向量
        3. 立即返回笔记 ID
        4. 若用户未提供 tags/category，后台异步任务自动生成
        """
        note_id = str(uuid.uuid4())
        note = Note(
            id=note_id,
            user_id=user_id,
            title=payload.title,
            content=payload.content,
            tags=payload.tags,
            category=payload.category,
        )
        db.add(note)
        await db.commit()
        await db.refresh(note)

        # 向量化写入 ChromaDB
        try:
            doc = Document(
                page_content=payload.content,
                metadata={
                    "user_id": user_id,
                    "note_id": note_id,
                    "doc_type": "note",
                    "title": payload.title,
                }
            )
            await asyncio.to_thread(lambda: self._notes_store.add_documents([doc], ids=[note_id]))
        except Exception as e:
            logger.error(f"笔记向量化失败 note_id={note_id}: {e}")

        # 若用户已提供 tags/category，跳过自动标签生成
        user_provided_meta = payload.tags is not None or payload.category is not None
        if not user_provided_meta:
            asyncio.create_task(self._auto_tag_and_review(note_id, user_id, payload.content))

        return self._doc_to_response(note)

    async def update_note(self, db: AsyncSession, note_id: str, user_id: str, payload: NoteUpdate) -> NoteResponse | None:
        """
        更新笔记：
        1. 更新 MySQL 中的 title/content/category/tags
        2. 如果 content 变更，删除旧向量并写入新向量
        """
        stmt = select(Note).where(Note.id == note_id, Note.user_id == user_id)
        result = await db.execute(stmt)
        note = result.scalar_one_or_none()
        if not note:
            return None

        content_changed = payload.content is not None

        if payload.title is not None:
            note.title = payload.title
        if payload.content is not None:
            note.content = payload.content
        if payload.category is not None:
            note.category = payload.category
        if payload.tags is not None:
            note.tags = payload.tags
        if payload.is_pinned is not None:
            note.is_pinned = payload.is_pinned

        await db.commit()
        await db.refresh(note)

        # content 变更时同步更新向量
        if content_changed:
            try:
                # 先删除旧向量，再写入新向量
                await asyncio.to_thread(
                    lambda: self._notes_store.delete(where={"note_id": note_id})
                )
                doc = Document(
                    page_content=note.content,
                    metadata={
                        "user_id": user_id,
                        "note_id": note_id,
                        "doc_type": "note",
                        "title": note.title,
                    }
                )
                await asyncio.to_thread(lambda: self._notes_store.add_documents([doc], ids=[note_id]))
            except Exception as e:
                logger.error(f"更新笔记向量失败 note_id={note_id}: {e}")

        return self._doc_to_response(note)

    async def delete_note(self, db: AsyncSession, note_id: str, user_id: str) -> bool:
        """
        删除笔记：
        1. 删除 MySQL 中的笔记（review_records 通过 FK CASCADE 自动删除）
        2. 删除 ChromaDB 中的向量
        """
        stmt = select(Note).where(Note.id == note_id, Note.user_id == user_id)
        result = await db.execute(stmt)
        note = result.scalar_one_or_none()
        if not note:
            return False

        await db.execute(delete(Note).where(Note.id == note_id, Note.user_id == user_id))
        await db.commit()

        # 清理向量
        try:
            await asyncio.to_thread(
                lambda: self._notes_store.delete(where={"note_id": note_id})
            )
        except Exception as e:
            logger.error(f"删除笔记向量失败 note_id={note_id}: {e}")

        return True

    async def get_note(self, db: AsyncSession, note_id: str, user_id: str) -> NoteResponse | None:
        """
        根据笔记 ID 和用户 ID 获取笔记详情。
        """
        stmt = select(Note).where(Note.id == note_id, Note.user_id == user_id)
        result = await db.execute(stmt)
        note = result.scalar_one_or_none()
        if not note:
            return None
        return self._doc_to_response(note)

    async def list_notes(
        self,
        db: AsyncSession,
        user_id: str,
        page: int = 1,
        page_size: int = 20,
        category: str | None = None,
        tag: str | None = None,
        sort_by: str = "updated_at",
    ) -> tuple[list[NoteResponse], int]:
        """
        分页查询笔记列表，支持按分类筛选和排序。tag 筛选为内存过滤。
        """
        conditions = [Note.user_id == user_id]
        if category:
            conditions.append(Note.category == category)

        # 先查总数
        count_stmt = select(func.count(Note.id)).where(*conditions)
        result = await db.execute(count_stmt)
        total = result.scalar() or 0

        sort_column = {
            "updated_at": Note.updated_at,
            "created_at": Note.created_at,
            "title": Note.title,
        }.get(sort_by, Note.updated_at)

        if sort_by == "title":
            order = sort_column.asc()
        else:
            order = sort_column.desc()

        # 分页查询，置顶优先 + 指定排序
        stmt = (
            select(Note)
            .where(*conditions)
            .order_by(Note.is_pinned.desc(), order)
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        result = await db.execute(stmt)
        notes = result.scalars().all()

        note_list = [self._doc_to_response(n) for n in notes]

        # tag 为 JSON 数组，在 Python 层面过滤
        if tag:
            note_list = [n for n in note_list if n.tags and tag in n.tags]

        return note_list, total

    async def search_notes(self, db: AsyncSession, user_id: str, query: str, top_k: int = 10) -> list[NoteResponse]:
        """
        语义搜索笔记：ChromaDB 向量检索 → MySQL 回填完整数据。
        只搜索当前用户的笔记（通过 metadata filter）。
        """
        try:
            docs = await asyncio.to_thread(
                self._notes_store.similarity_search,
                query,
                k=top_k,
                filter={"$and": [{"user_id": user_id}, {"doc_type": "note"}]},
            )
        except Exception as e:
            logger.error(f"笔记语义搜索失败: {e}")
            return []

        note_ids = [doc.metadata.get("note_id") for doc in docs if doc.metadata.get("note_id")]
        if not note_ids:
            return []

        # 从 MySQL 获取完整笔记信息并保持向量检索的顺序
        stmt = select(Note).where(Note.id.in_(note_ids), Note.user_id == user_id)
        result = await db.execute(stmt)
        notes_map = {n.id: n for n in result.scalars().all()}

        sorted_notes = []
        for nid in note_ids:
            if nid in notes_map:
                sorted_notes.append(self._doc_to_response(notes_map[nid]))

        return sorted_notes

    async def get_related_notes(
        self,
        db: AsyncSession,
        note_id: str,
        user_id: str,
        top_k: int = 3,
    ) -> list[dict]:
        """
        获取与当前笔记语义相似的其他笔记和知识库文档。

        检索流程：
        1. 用笔记内容同时在 notes_collection 和 rag_collection 检索
        2. 合并结果并使用 reorder_service 重排序
        3. 标注来源（note / knowledge_base）
        """
        note = await self.get_note(db, note_id, user_id)
        if not note:
            return []

        related_items = []

        # 从笔记库检索相似笔记（排除自身）
        try:
            note_docs = await asyncio.to_thread(
                self._notes_store.similarity_search_with_score,
                note.content,
                k=top_k + 1,  # 多取一个，排除自身
            )
            for doc, score in note_docs:
                meta_note_id = doc.metadata.get("note_id", "")
                if meta_note_id == note_id:
                    continue
                related_items.append({
                    "id": meta_note_id,
                    "title": doc.metadata.get("title", "无标题"),
                    "content_preview": doc.page_content[:150],
                    "content": doc.page_content,
                    "similarity": round(score, 4),
                    "source": "note",
                })
        except Exception as e:
            logger.error(f"从笔记库检索关联笔记失败: {e}")

        # 从知识库检索相关文档
        try:
            from app.rag.vector_store import VectorStoreService
            vector_store = VectorStoreService()
            # 直接使用 vectors_store 的 similarity_search_with_score
            kb_docs = await asyncio.to_thread(
                vector_store.vectors_store.similarity_search_with_score,
                note.content,
                k=top_k,
                filter={"user_id": user_id},
            )
            for doc, score in kb_docs:
                related_items.append({
                    "id": doc.metadata.get("source", doc.metadata.get("filename", "")),
                    "title": doc.metadata.get("original_filename", doc.metadata.get("source", "知识库文档")),
                    "content_preview": doc.page_content[:150],
                    "content": doc.page_content,  # 完整切片内容，供前端内联展开查看
                    "similarity": round(score, 4),
                    "source": "knowledge_base",
                })
        except Exception as e:
            logger.error(f"从知识库检索关联文档失败: {e}")

        # 按相似度降序排序（分数越低越相似），取 top_k
        related_items.sort(key=lambda x: x["similarity"])
        return related_items[:top_k]

    @staticmethod
    def _extract_json(text: str) -> str:
        """
        从 LLM 输出中提取 JSON 字符串。
        处理以下情况：
        - JSON 被 markdown 代码块包裹（```json ... ```）
        - JSON 前面有文字描述
        - JSON 后面有文字描述
        """
        import re

        # 尝试匹配 markdown 代码块中的 JSON
        match = re.search(r'```(?:json)?\s*\n(.*?)\n\s*```', text, re.DOTALL)
        if match:
            return match.group(1).strip()

        # 尝试从第一个 { 到最后一个 } 提取 JSON
        start = text.find('{')
        end = text.rfind('}')
        if start != -1 and end != -1 and end > start:
            return text[start:end + 1]

        return text

    async def _auto_tag_and_review(self, note_id: str, user_id: str, content: str):
        """
        后台异步任务：LLM 分析笔记内容 → 生成标签和分类 → 更新 MySQL → 创建回顾记录。

        此方法在 create_note 结束后通过 asyncio.create_task 执行，
        不阻塞用户保存响应。标签延迟出现是设计意图。
        """
        try:
            # 加载 prompt 模板并填充笔记内容
            prompt_template = load_prompt("auto_tag_prompt")
            prompt = prompt_template.replace("{content}", content)

            # 惰性导入避免模块级循环依赖
            from app.core.background_init import init_manager
            from app.db.db_config import AsyncSessionLocal
            chat_model = init_manager.chat_model

            response = await chat_model.ainvoke([HumanMessage(content=prompt)])
            raw_output = response.content.strip()

            # 提取 JSON：LLM 输出可能包含前言、markdown代码块等
            json_str = self._extract_json(raw_output)

            # 解析 LLM 返回的 JSON
            result = json.loads(json_str)
            tags = result.get("tags", [])
            category = result.get("category", "life")

            logger.info(f"自动标签生成完成 note_id={note_id}, tags={tags}, category={category}")

            # 写入 MySQL
            async with AsyncSessionLocal() as session:
                stmt = (
                    update(Note)
                    .where(Note.id == note_id, Note.user_id == user_id)
                    .values(tags=tags, category=category)
                )
                await session.execute(stmt)

                # 创建回顾记录（首次间隔 1 天）
                now = datetime.now()
                review = ReviewRecord(
                    id=str(uuid.uuid4()),
                    note_id=note_id,
                    user_id=user_id,
                    next_review_at=now + timedelta(days=1),
                    interval_days=1,
                    review_count=0,
                )
                session.add(review)
                await session.commit()

        except json.JSONDecodeError as e:
            logger.error(f"解析 LLM 标签输出失败 note_id={note_id}, raw={raw_output[:200]}, extracted={json_str[:200]}: {e}")
        except Exception as e:
            logger.error(f"自动标签后台任务失败 note_id={note_id}: {e}")

    async def autocomplete(self, context: str) -> dict:
        """
        AI 内联补全 —— 基于光标前上下文，调用 Ollama qwen3.5:0.8b 快速生成续写文本。

        Args:
            context: 光标前的文本上下文（最多 50 字）

        Returns:
            {"completion": "续写文本", "success": true/false}
        """
        try:
            from langchain_core.messages import HumanMessage

            from app.core.background_init import init_manager
            chat_model = init_manager.chat_model

            prompt_template = load_prompt("autocomplete_prompt")
            prompt = prompt_template.format(context=context[-200:])  # 最多取最后200字
            response = await chat_model.ainvoke([HumanMessage(content=prompt)])
            completion = response.content.strip()

            # 防止回复重复已有内容
            if completion and context.endswith(completion[:10]):
                completion = completion[10:]

            return {"success": True, "completion": completion}
        except Exception as e:
            logger.error(f"内联补全失败: {e}")
            return {"success": False, "completion": ""}

    async def assist_stream(self, content: str, action: str):
        """
        AI 写作辅助 SSE 流式输出 —— 支持续写/缩写/扩写三种模式。

        Args:
            content: 用户选中的文本
            action: 操作类型 (expand / summarize / continue)

        Yields:
            SSE 事件数据（字符串）
        """
        from langchain_core.messages import HumanMessage

        from app.core.background_init import init_manager
        chat_model = init_manager.chat_model

        prompt_template = load_prompt("write_assistant_prompt")
        prompt = prompt_template.format(content=content, action=action)

        try:
            async for chunk in chat_model.astream([HumanMessage(content=prompt)]):
                if chunk.content:
                    yield f"data: {chunk.content}\n\n"
            yield "data: [DONE]\n\n"
        except Exception as e:
            logger.error(f"写作辅助流式输出失败: {e}")
            yield f"data: [ERROR: {str(e)}]\n\n"

    async def get_category_stats(self, db: AsyncSession, user_id: str) -> dict:
        """
        获取用户的笔记分类统计 —— 动态查询所有存在的分类并计数。
        """
        stmt = select(Note.category, func.count(Note.id)).where(
            Note.user_id == user_id,
            Note.category.isnot(None),
        ).group_by(Note.category)
        result = await db.execute(stmt)
        categories = [{"category": cat, "count": count} for cat, count in result]

        count_stmt = select(func.count(Note.id)).where(
            Note.user_id == user_id,
            Note.category.is_(None),
        )
        result = await db.execute(count_stmt)
        uncategorized = result.scalar() or 0

        total_stmt = select(func.count(Note.id)).where(Note.user_id == user_id)
        result = await db.execute(total_stmt)
        total = result.scalar() or 0

        return {
            "total": total,
            "categories": categories,
            "uncategorized": uncategorized,
        }

    async def delete_category(self, db: AsyncSession, user_id: str, category: str) -> int:
        """
        删除某个分类及其下所有笔记。
        返回被删除的笔记数量。
        """
        stmt = select(Note).where(
            Note.user_id == user_id,
            Note.category == category,
        )
        result = await db.execute(stmt)
        notes = result.scalars().all()
        note_ids = [n.id for n in notes]
        if not note_ids:
            return 0

        await db.execute(
            delete(Note).where(Note.user_id == user_id, Note.category == category)
        )
        await db.commit()

        for nid in note_ids:
            try:
                await asyncio.to_thread(
                    lambda id=nid: self._notes_store.delete(where={"note_id": id})
                )
            except Exception as e:
                logger.error(f"删除分类笔记向量失败 note_id={nid}: {e}")

        return len(note_ids)

    async def export_note_markdown(self, db: AsyncSession, note_id: str, user_id: str) -> str | None:
        """
        导出单篇笔记为 Markdown 文本。
        包含 frontmatter 格式的元数据（标题、标签、分类、日期）。
        """
        note = await self.get_note(db, note_id, user_id)
        if not note:
            return None

        lines = ["---"]
        lines.append(f"title: {note.title}")
        if note.tags:
            lines.append(f"tags: [{', '.join(note.tags)}]")
        if note.category:
            lines.append(f"category: {note.category}")
        lines.append(f"created_at: {note.created_at}")
        lines.append(f"updated_at: {note.updated_at}")
        lines.append("---")
        lines.append("")
        lines.append(f"# {note.title}")
        lines.append("")
        lines.append(note.content)

        return "\n".join(lines)


    async def batch_delete_notes(self, db: AsyncSession, user_id: str, note_ids: list[str]) -> int:
        """
        批量删除笔记：
        1. MySQL 批量删除（级联 review_records）
        2. ChromaDB 逐个清理向量
        返回实际删除数量。
        """
        if not note_ids:
            return 0

        stmt = select(Note).where(Note.id.in_(note_ids), Note.user_id == user_id)
        result = await db.execute(stmt)
        existing = result.scalars().all()
        existing_ids = [n.id for n in existing]

        if not existing_ids:
            return 0

        await db.execute(delete(Note).where(Note.id.in_(existing_ids), Note.user_id == user_id))
        await db.commit()

        for nid in existing_ids:
            try:
                await asyncio.to_thread(
                    lambda id=nid: self._notes_store.delete(where={"note_id": id})
                )
            except Exception as e:
                logger.error(f"批量删除向量失败 note_id={nid}: {e}")

        return len(existing_ids)

    async def batch_update_category(
        self, db: AsyncSession, user_id: str, note_ids: list[str], category: str
    ) -> int:
        """
        批量更新笔记分类。
        返回实际更新的数量。
        """
        if not note_ids:
            return 0

        stmt = (
            update(Note)
            .where(Note.id.in_(note_ids), Note.user_id == user_id)
            .values(category=category)
        )
        result = await db.execute(stmt)
        await db.commit()
        return result.rowcount

    async def batch_export_zip(self, db: AsyncSession, user_id: str, note_ids: list[str]) -> bytes:
        """
        批量导出笔记为 ZIP 压缩包（内含 .md 文件）。
        """
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
            for nid in note_ids:
                note = await self.get_note(db, nid, user_id)
                if not note:
                    continue
                md = await self.export_note_markdown(db, nid, user_id)
                if not md:
                    continue
                safe_title = re.sub(r'[\\/:*?"<>|]', '_', note.title or nid)[:80]
                zf.writestr(f"{safe_title}.md", md.encode("utf-8"))
        buf.seek(0)
        return buf.getvalue()

    async def batch_update_pin(
        self, db: AsyncSession, user_id: str, note_ids: list[str], is_pinned: bool
    ) -> int:
        """
        批量置顶/取消置顶笔记。
        返回实际更新的数量。
        """
        if not note_ids:
            return 0

        stmt = (
            update(Note)
            .where(Note.id.in_(note_ids), Note.user_id == user_id)
            .values(is_pinned=is_pinned)
        )
        result = await db.execute(stmt)
        await db.commit()
        return result.rowcount


_note_service_instance: "NoteService | None" = None


def get_note_service() -> NoteService:
    """依赖注入工厂函数。"""
    global _note_service_instance
    if _note_service_instance is None:
        from app.core.background_init import init_manager
        _note_service_instance = init_manager.note_service
    return _note_service_instance
