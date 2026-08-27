"""图谱业务编排：抽取触发、抽取管线、生命周期清理。

抽取管线在 create_task 火后不管的任务内执行，自开 AsyncSessionLocal 会话；
失败不阻塞笔记主流程，写回 extract_logs(failed) 并推送失败事件。
"""
import asyncio
import hashlib
import uuid
from datetime import datetime

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logger_handler import logger
from app.db.db_config import AsyncSessionLocal
from app.graph.extraction.entity_extractor import extract_entities
from app.graph.extraction.link_parser import parse_links
from app.graph.schemas.graph import EntityIn, RelationIn
from app.graph.services.event_bus import event_bus
from app.graph.storage import get_graph_store
from app.models.graph import (
    GraphDoc,
    GraphEntityNote,
    GraphExtractLog,
    GraphNoteEdge,
)
from app.models.note import Note


def content_hash(content: str) -> str:
    return hashlib.md5((content or "").encode("utf-8")).hexdigest()


async def _ensure_extract_log(db: AsyncSession, user_id: str, source_id: str, content_hash_value: str,
                              source_type: str = "note") -> bool:
    """返回 False 表示哈希相同需跳过；True 表示继续抽取（写入 pending）。

    source_id 为笔记 UUID 或文档 md5；source_type 为 note/doc。
    """
    row = (await db.execute(
        select(GraphExtractLog).where(GraphExtractLog.user_id == user_id,
                                      GraphExtractLog.note_id == source_id,
                                      GraphExtractLog.source_type == source_type))).scalar_one_or_none()
    if row is not None and row.status != "failed":
        if row.content_hash == content_hash_value:
            return False
    if row is None:
        db.add(GraphExtractLog(id=str(uuid.uuid4()), user_id=user_id, note_id=source_id,
                               source_type=source_type, content_hash=content_hash_value, status="pending"))
    else:
        row.content_hash = content_hash_value
        row.status = "pending"
        row.error_message = None
    try:
        await db.commit()
    except IntegrityError:
        # 并发竞态：两个调度同时插入同一 (user_id, note_id, source_type)，
        # 唯一约束冲突 → 回滚重查复用（按新行语义继续抽取）。
        await db.rollback()
        row = (await db.execute(
            select(GraphExtractLog).where(GraphExtractLog.user_id == user_id,
                                          GraphExtractLog.note_id == source_id,
                                          GraphExtractLog.source_type == source_type))).scalar_one()
        row.content_hash = content_hash_value
        row.status = "pending"
        row.error_message = None
        await db.commit()
    return True


async def _run_extraction(source_id: str, user_id: str, title: str, body_hash: str,
                          body: str = None, source_type: str = "note"):
    """抽取管线：LLM 抽取 → upsert 实体/关系 → 先清后插来源关联 → 落日志 → 推送。

    source_type="note" 时额外做双链解析与笔记双链边；"doc" 时不做双链（文档不入笔记图）。
    """
    try:
        async with AsyncSessionLocal() as db:
            if body is None:
                if source_type == "doc":
                    logger.error(f"文档抽取缺少正文 source_id={source_id}")
                    return
                note = (await db.execute(
                    select(Note).where(Note.id == source_id, Note.user_id == user_id))).scalar_one_or_none()
                if note is None:
                    return
                title = title or note.title
                body = note.content
                body_hash = content_hash(body)
            store = get_graph_store(db)

            # 1. 双链解析（仅笔记；同步、快）
            links = parse_links(body) if source_type == "note" else []

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
                    source_note_ids=[source_id]))
                name_to_id[ent.name] = e.id
                new_count += 1  # upsert 语义下简化为计数
            for rel in result.relations:
                src = name_to_id.get(rel.source)
                tgt = name_to_id.get(rel.target)
                if src and tgt:
                    await store.create_relation(user_id, RelationIn(
                        source_id=src, target_id=tgt, relation_type=rel.relation_type,
                        confidence=0.7))

            # 5. 来源关联：先清后插（幂等覆盖 mentions）
            await db.execute(delete(GraphEntityNote).where(
                GraphEntityNote.user_id == user_id, GraphEntityNote.note_id == source_id,
                GraphEntityNote.source_type == source_type))
            for ent in result.entities:
                eid = name_to_id.get(ent.name)
                if eid:
                    db.add(GraphEntityNote(id=str(uuid.uuid4()), user_id=user_id, entity_id=eid,
                                           note_id=source_id, source_type=source_type,
                                           mention_count=len(ent.mentions),
                                           context=[{"snippet": m} for m in ent.mentions]))

            # 6. 笔记双链边：先清后插（仅出边，目标笔记需存在），并清理过期反向边（仅笔记）
            if source_type == "note":
                target_ids: set[str] = set()
                await db.execute(delete(GraphNoteEdge).where(
                    GraphNoteEdge.user_id == user_id, GraphNoteEdge.source_note_id == source_id))
                for link in links:
                    target = (await db.execute(
                        select(Note).where(Note.user_id == user_id, Note.title == link))).scalars().first()
                    if target:
                        target_ids.add(target.id)
                        db.add(GraphNoteEdge(id=str(uuid.uuid4()), user_id=user_id,
                                             source_note_id=source_id, target_note_id=target.id, kind="wiki"))
                        # 双向连通：被引用侧挂"被引用"边
                        exists = (await db.execute(select(GraphNoteEdge).where(
                            GraphNoteEdge.user_id == user_id, GraphNoteEdge.source_note_id == target.id,
                            GraphNoteEdge.target_note_id == source_id))).scalar_one_or_none()
                        if not exists:
                            db.add(GraphNoteEdge(id=str(uuid.uuid4()), user_id=user_id,
                                                 source_note_id=target.id, target_note_id=source_id, kind="wiki"))
                # 清理过期反向边：本笔记曾引用、现已移除链接的目标侧残留的"被引用"边
                await db.execute(delete(GraphNoteEdge).where(
                    GraphNoteEdge.user_id == user_id, GraphNoteEdge.target_note_id == source_id,
                    GraphNoteEdge.source_note_id.notin_(target_ids)))

            # 7. 落日志
            log = (await db.execute(select(GraphExtractLog).where(
                GraphExtractLog.user_id == user_id, GraphExtractLog.note_id == source_id,
                GraphExtractLog.source_type == source_type))).scalar_one()
            log.status = "success"
            log.content_hash = body_hash
            log.new_count = new_count
            log.update_count = update_count
            log.finished_at = datetime.now()
            await db.commit()

            await event_bus.publish(user_id, {"type": "extract_done", "note_id": source_id,
                                              "source_type": source_type, "status": "success",
                                              "new_count": new_count, "update_count": update_count})
    except Exception as e:
        logger.error(f"实体抽取失败 source_id={source_id} type={source_type}: {e}", exc_info=True)
        try:
            async with AsyncSessionLocal() as db:
                log = (await db.execute(select(GraphExtractLog).where(
                    GraphExtractLog.user_id == user_id, GraphExtractLog.note_id == source_id,
                    GraphExtractLog.source_type == source_type))).scalar_one_or_none()
                if log:
                    log.status = "failed"
                    log.error_message = str(e)[:500]
                    log.finished_at = datetime.now()
                    await db.commit()
            await event_bus.publish(user_id, {"type": "extract_failed", "note_id": source_id,
                                              "source_type": source_type, "status": "failed",
                                              "error": str(e)[:200]})
        except Exception as e2:
            logger.error(f"写失败日志/推送失败 source_id={source_id}: {e2}")


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


async def maybe_schedule_doc_extraction(user_id: str, md5: str, filename: str, content: str) -> bool:
    """知识库文档抽取触发（上传成功后由知识库管线调用）：

    1. upsert graph_docs 文档节点元数据（id=md5，幂等）；
    2. 内容哈希增量触发（extract_logs source_type='doc' 哈希相同则跳过）；
    3. 触发则 create_task 跑抽取管线（source_type='doc'，不做双链边）。
    返回是否触发。失败不阻塞上传主流程。
    """
    body_hash = content_hash(content)
    async with AsyncSessionLocal() as db:
        doc = (await db.execute(select(GraphDoc).where(
            GraphDoc.id == md5, GraphDoc.user_id == user_id))).scalar_one_or_none()
        if doc is None:
            db.add(GraphDoc(id=md5, user_id=user_id, filename=filename))
        elif doc.filename != filename:
            doc.filename = filename
        # 先落文档节点（哈希一致跳过抽取时 _ensure_extract_log 不 commit，避免节点被回滚）
        await db.commit()
        proceed = await _ensure_extract_log(db, user_id, md5, body_hash, source_type="doc")
    if not proceed:
        return False
    asyncio.create_task(_run_extraction(md5, user_id, filename, body_hash, body=content, source_type="doc"))
    return True


async def cleanup_doc_graph(user_id: str, doc_id: str) -> None:
    """知识库文档删除联动：清理该文档的节点元数据、实体关联、抽取日志（独立会话，异常不外抛）。

    实体本身保留（与其他笔记/文档共享）。与 cleanup_note_graph 不同，文档删除走知识库
    服务（无 DB 会话），故自开 AsyncSessionLocal 会话。
    """
    try:
        async with AsyncSessionLocal() as db:
            await db.execute(delete(GraphDoc).where(
                GraphDoc.user_id == user_id, GraphDoc.id == doc_id))
            await db.execute(delete(GraphEntityNote).where(
                GraphEntityNote.user_id == user_id, GraphEntityNote.note_id == doc_id,
                GraphEntityNote.source_type == "doc"))
            await db.execute(delete(GraphExtractLog).where(
                GraphExtractLog.user_id == user_id, GraphExtractLog.note_id == doc_id,
                GraphExtractLog.source_type == "doc"))
            await db.commit()
    except Exception as e:
        logger.error(f"清理文档图谱失败 user_id={user_id} doc_id={doc_id}: {e}")


async def cleanup_all_docs_graph(user_id: str) -> None:
    """清空用户全部文档的图谱数据（清空知识库时调用，独立会话，异常不外抛）。"""
    try:
        async with AsyncSessionLocal() as db:
            await db.execute(delete(GraphDoc).where(GraphDoc.user_id == user_id))
            await db.execute(delete(GraphEntityNote).where(
                GraphEntityNote.user_id == user_id, GraphEntityNote.source_type == "doc"))
            await db.execute(delete(GraphExtractLog).where(
                GraphExtractLog.user_id == user_id, GraphExtractLog.source_type == "doc"))
            await db.commit()
    except Exception as e:
        logger.error(f"清空用户文档图谱失败 user_id={user_id}: {e}")
