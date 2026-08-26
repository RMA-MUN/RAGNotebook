"""图谱业务编排：抽取触发、抽取管线、生命周期清理。

抽取管线在 create_task 火后不管的任务内执行，自开 AsyncSessionLocal 会话；
失败不阻塞笔记主流程，写回 extract_logs(failed) 并推送失败事件。
"""
import asyncio
import hashlib
import uuid
from datetime import datetime

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logger_handler import logger
from app.db.db_config import AsyncSessionLocal
from app.graph.extraction.entity_extractor import extract_entities
from app.graph.extraction.link_parser import parse_links
from app.graph.schemas.graph import EntityIn, RelationIn
from app.graph.services.event_bus import event_bus
from app.graph.storage import get_graph_store
from app.models.graph import (
    GraphEntity,
    GraphEntityNote,
    GraphExtractLog,
    GraphNoteEdge,
    GraphRelation,
)
from app.models.note import Note


def content_hash(content: str) -> str:
    return hashlib.md5((content or "").encode("utf-8")).hexdigest()


async def _ensure_extract_log(db: AsyncSession, user_id: str, note_id: str, content_hash_value: str) -> bool:
    """返回 False 表示哈希相同需跳过；True 表示继续抽取（写入 pending）。"""
    row = (await db.execute(
        select(GraphExtractLog).where(GraphExtractLog.user_id == user_id,
                                      GraphExtractLog.note_id == note_id))).scalar_one_or_none()
    if row is not None and row.status != "failed":
        if row.content_hash == content_hash_value:
            return False
    if row is None:
        db.add(GraphExtractLog(id=str(uuid.uuid4()), user_id=user_id, note_id=note_id,
                               content_hash=content_hash_value, status="pending"))
    else:
        row.content_hash = content_hash_value
        row.status = "pending"
        row.error_message = None
    await db.commit()
    return True


async def _run_extraction(note_id: str, user_id: str, title: str, body_hash: str, body: str = None):
    """抽取管线：双链解析 → LLM 抽取 → upsert 实体/关系 → 先清后插实体笔记关联 → 双链边 → 落日志 → 推送。"""
    try:
        async with AsyncSessionLocal() as db:
            if body is None:
                note = (await db.execute(
                    select(Note).where(Note.id == note_id, Note.user_id == user_id))).scalar_one_or_none()
                if note is None:
                    return
                title = title or note.title
                body = note.content
                body_hash = content_hash(body)
            store = get_graph_store(db)

            # 1. 双链解析（同步，快）
            links = parse_links(body)

            # 2. LLM 抽取
            from app.core.background_init import init_manager
            chat_model = init_manager.chat_model
            if chat_model is None:
                raise RuntimeError("chat_model 未初始化")
            result = await extract_entities(title, body, chat_model)

            # 3/4. upsert 实体 + 关系（relation 依赖实体 id 映射）
            name_to_id: dict[str, str] = {}
            new_count = 0
            update_count = 0
            for ent in result.entities:
                e = await store.upsert_entity(user_id, EntityIn(
                    name=ent.name, display_name=ent.name, aliases=ent.aliases,
                    description=ent.description, confidence=0.8,
                    source_note_ids=[note_id]))
                name_to_id[ent.name] = e.id
                new_count += 1  # upsert 语义下简化为计数
            for rel in result.relations:
                src = name_to_id.get(rel.source)
                tgt = name_to_id.get(rel.target)
                if src and tgt:
                    await store.create_relation(user_id, RelationIn(
                        source_id=src, target_id=tgt, relation_type=rel.relation_type,
                        confidence=0.7))

            # 5. 实体↔笔记关联：先清后插（幂等覆盖 mentions）
            await db.execute(delete(GraphEntityNote).where(
                GraphEntityNote.user_id == user_id, GraphEntityNote.note_id == note_id))
            for ent in result.entities:
                eid = name_to_id.get(ent.name)
                if eid:
                    db.add(GraphEntityNote(id=str(uuid.uuid4()), user_id=user_id, entity_id=eid,
                                           note_id=note_id, mention_count=len(ent.mentions),
                                           context=[{"snippet": m} for m in ent.mentions]))

            # 6. 双链边：先清后插（仅出边，目标笔记需存在）
            await db.execute(delete(GraphNoteEdge).where(
                GraphNoteEdge.user_id == user_id, GraphNoteEdge.source_note_id == note_id))
            for link in links:
                target = (await db.execute(
                    select(Note).where(Note.user_id == user_id, Note.title == link))).scalar_one_or_none()
                if target:
                    db.add(GraphNoteEdge(id=str(uuid.uuid4()), user_id=user_id,
                                         source_note_id=note_id, target_note_id=target.id, kind="wiki"))
                    # 双向连通：被引用侧挂"被引用"边
                    exists = (await db.execute(select(GraphNoteEdge).where(
                        GraphNoteEdge.user_id == user_id, GraphNoteEdge.source_note_id == target.id,
                        GraphNoteEdge.target_note_id == note_id))).scalar_one_or_none()
                    if not exists:
                        db.add(GraphNoteEdge(id=str(uuid.uuid4()), user_id=user_id,
                                             source_note_id=target.id, target_note_id=note_id, kind="wiki"))

            # 7. 落日志
            log = (await db.execute(select(GraphExtractLog).where(
                GraphExtractLog.user_id == user_id, GraphExtractLog.note_id == note_id))).scalar_one()
            log.status = "success"
            log.content_hash = body_hash
            log.new_count = new_count
            log.update_count = update_count
            log.finished_at = datetime.now()
            await db.commit()

            await event_bus.publish(user_id, {"type": "extract_done", "note_id": note_id,
                                              "status": "success", "new_count": new_count,
                                              "update_count": update_count})
    except Exception as e:
        logger.error(f"实体抽取失败 note_id={note_id}: {e}", exc_info=True)
        try:
            async with AsyncSessionLocal() as db:
                log = (await db.execute(select(GraphExtractLog).where(
                    GraphExtractLog.user_id == user_id, GraphExtractLog.note_id == note_id))).scalar_one_or_none()
                if log:
                    log.status = "failed"
                    log.error_message = str(e)[:500]
                    log.finished_at = datetime.now()
                    await db.commit()
            await event_bus.publish(user_id, {"type": "extract_failed", "note_id": note_id,
                                              "status": "failed", "error": str(e)[:200]})
        except Exception as e2:
            logger.error(f"写失败日志/推送失败 note_id={note_id}: {e2}")


async def maybe_schedule_extraction(note_id: str, user_id: str, title: str, content: str) -> bool:
    """内容哈希增量触发：哈希变化才 create_task 抽取。返回是否触发。"""
    body_hash = content_hash(content)
    async with AsyncSessionLocal() as db:
        proceed = await _ensure_extract_log(db, user_id, note_id, body_hash)
    if not proceed:
        return False
    asyncio.create_task(_run_extraction(note_id, user_id, title, body_hash, body=content))
    return True


async def manual_re_extract(note_id: str, user_id: str) -> bool:
    """手动重抽：强制触发（extract_logs 置 pending 并跑管线）。"""
    async with AsyncSessionLocal() as db:
        row = (await db.execute(select(GraphExtractLog).where(
            GraphExtractLog.user_id == user_id, GraphExtractLog.note_id == note_id))).scalar_one_or_none()
        h = row.content_hash if row else content_hash("")
        if row is None:
            db.add(GraphExtractLog(id=str(uuid.uuid4()), user_id=user_id, note_id=note_id,
                                   content_hash=h, status="pending"))
        else:
            row.status = "pending"
            row.error_message = None
        await db.commit()
    asyncio.create_task(_run_extraction(note_id, user_id, "", None, body=None))
    return True


async def cleanup_note_graph(db: AsyncSession, user_id: str, note_id: str) -> None:
    """笔记删除联动：清理该笔记的入/出双链边、实体关联、抽取日志（事务内调用）。"""
    await db.execute(delete(GraphNoteEdge).where(
        GraphNoteEdge.user_id == user_id,
        (GraphNoteEdge.source_note_id == note_id) | (GraphNoteEdge.target_note_id == note_id)))
    await db.execute(delete(GraphEntityNote).where(
        GraphEntityNote.user_id == user_id, GraphEntityNote.note_id == note_id))
    await db.execute(delete(GraphExtractLog).where(
        GraphExtractLog.user_id == user_id, GraphExtractLog.note_id == note_id))