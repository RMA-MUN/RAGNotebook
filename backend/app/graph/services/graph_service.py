"""图谱业务编排：抽取触发、抽取管线、生命周期清理。

抽取管线在 create_task 火后不管的任务内执行，自开 AsyncSessionLocal 会话；
失败不阻塞笔记主流程，写回 extract_logs(failed) 并推送失败事件。
"""
import asyncio
import hashlib
import json
import uuid
from datetime import datetime

from sqlalchemy import delete, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logger_handler import logger
from app.db.db_config import AsyncSessionLocal
from app.graph.extraction.chunk_matcher import build_text_chunks, match_entities_in_chunks
from app.graph.extraction.entity_extractor import extract_entities
from app.graph.extraction.link_parser import parse_links
from app.graph.schemas.graph import EntityIn
from app.graph.services.event_bus import event_bus
from app.graph.storage import get_graph_store
from app.graph.storage.neo4j_graph_store import Neo4jGraphStore
from app.models.graph import (
    GraphBuildTask,
    GraphDoc,
    GraphEntity,
    GraphEntityNote,
    GraphExtractLog,
    GraphNoteEdge,
    GraphRelation,
)
from app.models.note import Note

# 任务失败重试上限（超过后置 failed，需手动重抽）
MAX_TASK_ATTEMPTS = 3


def content_hash(content: str) -> str:
    """抽取内容哈希（MD5）：任务判重与抽取日志幂等的依据，内容不变不重抽。"""
    return hashlib.md5((content or "").encode("utf-8")).hexdigest()


async def _type_name_to_id(store, user_id: str) -> dict[str, str]:
    """把 LLM 返回的语义类型名（person/tech/concept/…）映射到类型 id。

    通过 store.list_types 获取（实现内部惰性种入系统预置类型），
    Neo4j 与 MySQL 两实现均适用，避免抽取时类型缺失而丢弃 type_id。
    """
    types = await store.list_types(user_id)
    return {t.name: t.id for t in types}


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
                                          GraphExtractLog.source_type == source_type))).scalar_one_or_none()
        if row is None:
            db.add(GraphExtractLog(id=str(uuid.uuid4()), user_id=user_id, note_id=source_id,
                                   source_type=source_type, content_hash=content_hash_value,
                                   status="pending"))
        else:
            row.content_hash = content_hash_value
            row.status = "pending"
            row.error_message = None
        await db.commit()
    return True


async def _run_extraction(source_id: str, user_id: str, title: str, body_hash: str,
                          body: str = None, source_type: str = "note",
                          chunks: list[dict] | None = None) -> bool:
    """抽取管线：LLM 抽取 → upsert 实体/关系 → 先清后插来源关联 → 落日志 → 推送。

    source_type="note" 时额外做双链解析与笔记双链边；"doc" 时不做双链（文档不入笔记图）。
    Neo4j 主路径额外写入 Chunk 节点与 Chunk 级 MENTIONS 边；chunks 为上传管线
    预切好的切片载荷（含 page/image_paths），未提供时对 body 现切（笔记路径）。
    MySQL 回落路径（测试）保持原 ORM 写入，不含 Chunk 能力。

    返回 True 表示任务完成（含"来源已删除/无正文"这类无事可做的正常终态），
    False 表示可重试的失败。
    """
    try:
        async with AsyncSessionLocal() as db:
            if body is None:
                if source_type == "doc":
                    logger.error(f"文档抽取缺少正文 source_id={source_id}")
                    return True
                note = (await db.execute(
                    select(Note).where(Note.id == source_id, Note.user_id == user_id))).scalar_one_or_none()
                if note is None:
                    return True
                title = title or note.title
                body = note.content
                body_hash = content_hash(body)
            store = get_graph_store(db)
            neo4j = store if isinstance(store, Neo4jGraphStore) else None

            # 1. 双链解析（仅笔记；同步、快）
            links = parse_links(body) if source_type == "note" else []

            # 2. LLM 抽取
            from app.core.background_init import init_manager
            chat_model = init_manager.chat_model
            if chat_model is None:
                raise RuntimeError("chat_model 未初始化")
            result = await extract_entities(title, body, chat_model)

            # 2.5 来源存在性复检：LLM 抽取期间来源可能已被删除（清理先于抽取完成），
            #     此时放弃写入，避免"复活"已清理的数据
            if source_type == "doc":
                alive = (await db.execute(select(GraphDoc.id).where(
                    GraphDoc.id == source_id, GraphDoc.user_id == user_id))).first()
            else:
                alive = (await db.execute(select(Note.id).where(
                    Note.id == source_id, Note.user_id == user_id))).first()
            if alive is None:
                # 清掉遗留的 pending 日志（不复活任何数据），提交后放弃
                await db.execute(delete(GraphExtractLog).where(
                    GraphExtractLog.user_id == user_id, GraphExtractLog.note_id == source_id,
                    GraphExtractLog.source_type == source_type))
                await db.commit()
                return True

            # 3/4. upsert 实体 + 关系（关系按来源溯源，先清后写）
            type_map = await _type_name_to_id(store, user_id)
            name_to_id: dict[str, str] = {}
            new_count = 0
            update_count = 0
            for ent in result.entities:
                e = await store.upsert_entity(user_id, EntityIn(
                    name=ent.name, display_name=ent.name, aliases=ent.aliases,
                    description=ent.description, confidence=0.8,
                    type_id=type_map.get(ent.type) if ent.type else None,
                    source_note_ids=[source_id]))
                name_to_id[ent.name] = e.id
                new_count += 1  # upsert 语义下简化为计数

            source_rels = [
                {"source_id": name_to_id[rel.source], "target_id": name_to_id[rel.target],
                 "relation_type": rel.relation_type, "confidence": 0.7}
                for rel in result.relations
                if rel.source in name_to_id and rel.target in name_to_id]
            if neo4j is not None:
                await neo4j.set_relations_from_source(user_id, source_type, source_id, source_rels)
            else:
                # MySQL 回落路径（测试兼容；Task 9 随 Chroma 一并移除）
                await db.execute(delete(GraphRelation).where(
                    GraphRelation.user_id == user_id, GraphRelation.source_note_id == source_id,
                    GraphRelation.source_type == source_type))
                for rel in source_rels:
                    db.add(GraphRelation(
                        id=str(uuid.uuid4()), user_id=user_id, source_id=rel["source_id"],
                        target_id=rel["target_id"], relation_type=rel["relation_type"],
                        confidence=rel["confidence"], source_note_id=source_id,
                        source_type=source_type))

            # 5. 来源级关联：先清后插（幂等覆盖 mentions）
            mention_links = [
                {"entity_id": name_to_id[ent.name], "mention_count": len(ent.mentions),
                 "context": [{"snippet": m} for m in ent.mentions]}
                for ent in result.entities if ent.name in name_to_id]
            if neo4j is not None:
                await neo4j.ensure_source_node(user_id, source_type, source_id, title)
                await neo4j.set_source_mentions(user_id, source_type, source_id, mention_links)
            else:
                await db.execute(delete(GraphEntityNote).where(
                    GraphEntityNote.user_id == user_id, GraphEntityNote.note_id == source_id,
                    GraphEntityNote.source_type == source_type))
                for link in mention_links:
                    db.add(GraphEntityNote(
                        id=str(uuid.uuid4()), user_id=user_id, entity_id=link["entity_id"],
                        note_id=source_id, source_type=source_type,
                        mention_count=link["mention_count"], context=link["context"]))

            # 5.5 Chunk 写入与 Chunk 级 MENTIONS（仅 Neo4j；规则匹配零 LLM 成本）
            if neo4j is not None:
                chunk_payloads = list(chunks) if chunks else [
                    {"chunk_index": i, "text": text}
                    for i, text in enumerate(await asyncio.to_thread(build_text_chunks, body or ""))]
                if chunk_payloads:
                    texts = [c["text"] for c in chunk_payloads]
                    embed_model = init_manager.embed_model
                    if embed_model is not None:
                        vectors = await asyncio.to_thread(embed_model.embed_documents, texts)
                        for chunk, vector in zip(chunk_payloads, vectors):
                            chunk["embedding"] = vector
                    await neo4j.upsert_chunks(user_id, source_type, source_id, title, chunk_payloads)
                    matched = match_entities_in_chunks(result.entities, texts)
                    await neo4j.set_chunk_mentions(user_id, source_type, source_id, [
                        {"entity_id": name_to_id[name], "chunk_indexes": idxs}
                        for name, idxs in matched.items() if name in name_to_id])

            # 6. 笔记双链边：先清后插出边（目标笔记需存在；Neo4j 单向存储、查询双向匹配）
            if source_type == "note":
                if neo4j is not None:
                    wiki_targets = []
                    for link in links:
                        target = (await db.execute(
                            select(Note).where(Note.user_id == user_id, Note.title == link))).scalars().first()
                        if target:
                            wiki_targets.append({"target_note_id": target.id, "target_title": link,
                                                 "kind": "wiki"})
                    await neo4j.set_note_wiki_edges(user_id, source_id, wiki_targets)
                else:
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

            # 7. 落日志（日志行缺失时自建——抽取期间被清理或从未持久化时不得崩溃）
            log = (await db.execute(select(GraphExtractLog).where(
                GraphExtractLog.user_id == user_id, GraphExtractLog.note_id == source_id,
                GraphExtractLog.source_type == source_type))).scalar_one_or_none()
            if log is None:
                log = GraphExtractLog(id=str(uuid.uuid4()), user_id=user_id, note_id=source_id,
                                      source_type=source_type, content_hash=body_hash, status="pending")
                db.add(log)
            log.status = "success"
            log.content_hash = body_hash
            log.new_count = new_count
            log.update_count = update_count
            log.finished_at = datetime.now()
            await db.commit()

            await event_bus.publish(user_id, {"type": "extract_done", "note_id": source_id,
                                              "source_type": source_type, "status": "success",
                                              "new_count": new_count, "update_count": update_count})
            return True
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
        return False


async def _enqueue_build_task(user_id: str, source_type: str, source_id: str, title: str,
                              body_hash: str, payload: dict, force: bool = False) -> bool:
    """任务表幂等 upsert（同来源重复触发覆盖载荷并置 pending；内容变化重置重试计数）。"""
    payload_json = json.dumps(payload, ensure_ascii=False)
    async with AsyncSessionLocal() as db:
        row = (await db.execute(select(GraphBuildTask).where(
            GraphBuildTask.user_id == user_id,
            GraphBuildTask.source_type == source_type,
            GraphBuildTask.source_id == source_id))).scalar_one_or_none()
        if row is None:
            db.add(GraphBuildTask(
                id=str(uuid.uuid4()), user_id=user_id, source_type=source_type,
                source_id=source_id, title=title, content_hash=body_hash,
                payload=payload_json, status="pending", force=force))
        else:
            content_changed = row.content_hash != body_hash
            row.title = title
            row.content_hash = body_hash
            row.payload = payload_json
            row.status = "pending"
            row.error_message = None
            row.force = force or row.force
            if content_changed:
                row.attempts = 0
        await db.commit()
    return True


async def maybe_schedule_extraction(note_id: str, user_id: str, title: str, content: str) -> bool:
    """笔记保存后触发：入队图谱构建任务（worker 异步消费；哈希判重在 worker 侧做）。"""
    return await _enqueue_build_task(user_id, "note", note_id, title,
                                     content_hash(content), {"text": content or ""})


async def maybe_schedule_doc_extraction(user_id: str, md5: str, filename: str, content: str,
                                        chunks: list[dict] | None = None) -> bool:
    """知识库文档抽取触发（上传成功后由知识库管线调用）：

    1. upsert graph_docs 文档注册行（id=md5，幂等）；
    2. 入队构建任务（payload 优先携带上传管线预切的 chunks，保留 page/图片元数据；
       未携带时 worker 对全文兜底现切）。
    返回是否入队。失败不阻塞上传主流程。
    """
    body_hash = content_hash(content)
    async with AsyncSessionLocal() as db:
        doc = (await db.execute(select(GraphDoc).where(
            GraphDoc.id == md5, GraphDoc.user_id == user_id))).scalar_one_or_none()
        if doc is None:
            db.add(GraphDoc(id=md5, user_id=user_id, filename=filename))
        elif doc.filename != filename:
            doc.filename = filename
        await db.commit()
    payload = {"chunks": chunks} if chunks else {"text": content or ""}
    return await _enqueue_build_task(user_id, "doc", md5, filename, body_hash, payload)


async def manual_re_extract(note_id: str, user_id: str) -> bool:
    """手动重抽：强制入队（worker 跳过内容哈希判重）。"""
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
    return await _enqueue_build_task(user_id, "note", note_id, "", h, {}, force=True)


async def process_task(task_id: str) -> bool:
    """执行单条已认领（running）的构建任务：哈希判重 → 跑抽取管线 → 回写任务状态。

    返回任务是否到达终态（completed/failed）；重试（重回 pending）返回 False。
    """
    async with AsyncSessionLocal() as db:
        task = (await db.execute(
            select(GraphBuildTask).where(GraphBuildTask.id == task_id))).scalar_one_or_none()
        if task is None or task.status != "running":
            return True
        user_id, source_type, source_id = task.user_id, task.source_type, task.source_id
        title, body_hash, force = task.title or "", task.content_hash, bool(task.force)
        payload_raw = task.payload
        attempts = task.attempts

        if not force:
            proceed = await _ensure_extract_log(db, user_id, source_id, body_hash, source_type)
            if not proceed:
                task.status = "completed"
                task.error_message = None
                await db.commit()
                return True
        else:
            # 强制重抽：extract_logs 置 pending，让管线正常落 success
            row = (await db.execute(select(GraphExtractLog).where(
                GraphExtractLog.user_id == user_id, GraphExtractLog.note_id == source_id,
                GraphExtractLog.source_type == source_type))).scalar_one_or_none()
            if row is not None:
                row.status = "pending"
                row.error_message = None
                await db.commit()

        task.attempts = attempts + 1
        await db.commit()

    try:
        payload = json.loads(payload_raw) if payload_raw else {}
    except ValueError:
        payload = {}
    if source_type == "doc":
        body = payload.get("text", "")
        chunks = payload.get("chunks")
    else:
        # 笔记：优先用入队时的正文（与 content_hash 一致）；force 重抽载荷为空 → 管线读 Note 表最新内容
        body = payload.get("text")
        chunks = None

    ok = await _run_extraction(source_id, user_id, title, body_hash,
                               body=body, source_type=source_type, chunks=chunks)

    async with AsyncSessionLocal() as db:
        task = (await db.execute(
            select(GraphBuildTask).where(GraphBuildTask.id == task_id))).scalar_one_or_none()
        if task is None:
            return True
        if ok:
            task.status = "completed"
            task.error_message = None
            await db.commit()
            return True
        if task.attempts < MAX_TASK_ATTEMPTS:
            # 重试：重回 pending；若 extract log 已被管线置 failed，_ensure_extract_log
            # 允许 failed 状态重新进入，无需额外处理
            task.status = "pending"
            task.error_message = "抽取失败，待重试"
            await db.commit()
            return False
        task.status = "failed"
        log = (await db.execute(select(GraphExtractLog).where(
            GraphExtractLog.user_id == user_id, GraphExtractLog.note_id == source_id,
            GraphExtractLog.source_type == source_type))).scalar_one_or_none()
        task.error_message = (log.error_message if log and log.error_message else "抽取失败")[:500]
        await db.commit()
        return True


async def _sweep_orphans(db: AsyncSession, user_id: str, candidate_entity_ids: list[str],
                         removed_source_ids: list[str]) -> None:
    """来源关联删除后的孤儿清扫：摘除 source_note_ids 中被删来源的引用；
    实体已无任何来源关联且引用为空 → 孤儿，删除实体及其全部关系。"""
    removed = set(removed_source_ids)
    if not removed:
        return
    for eid in set(candidate_entity_ids):
        entity = (await db.execute(select(GraphEntity).where(
            GraphEntity.user_id == user_id, GraphEntity.id == eid))).scalar_one_or_none()
        if entity is None:
            continue
        remaining = [x for x in (entity.source_note_ids or []) if x not in removed]
        entity.source_note_ids = remaining
        has_link = (await db.execute(select(GraphEntityNote.id).where(
            GraphEntityNote.user_id == user_id, GraphEntityNote.entity_id == eid))).first()
        if has_link is None and not remaining:
            await db.execute(delete(GraphRelation).where(
                GraphRelation.user_id == user_id,
                or_(GraphRelation.source_id == eid, GraphRelation.target_id == eid)))
            await db.delete(entity)


async def cleanup_note_graph(db: AsyncSession, user_id: str, note_id: str) -> None:
    """笔记删除联动：清理该笔记的双链边、实体关联、Chunk、抽取日志、溯源关系，
    并清扫因该笔记而成为孤儿的实体（事务内调用）。"""
    store = get_graph_store(db)
    if isinstance(store, Neo4jGraphStore):
        candidates = await store.clear_source_data(user_id, "note", note_id)
        await store.sweep_orphan_entities(user_id, candidates, [note_id])
        await db.execute(delete(GraphExtractLog).where(
            GraphExtractLog.user_id == user_id, GraphExtractLog.note_id == note_id))
        return

    # MySQL 回落路径（测试兼容；Task 9 随 Chroma 一并移除）
    candidate_ids = (await db.execute(
        select(GraphEntityNote.entity_id).where(
            GraphEntityNote.user_id == user_id, GraphEntityNote.note_id == note_id))).scalars().all()
    await db.execute(delete(GraphNoteEdge).where(
        GraphNoteEdge.user_id == user_id,
        (GraphNoteEdge.source_note_id == note_id) | (GraphNoteEdge.target_note_id == note_id)))
    await db.execute(delete(GraphEntityNote).where(
        GraphEntityNote.user_id == user_id, GraphEntityNote.note_id == note_id))
    await db.execute(delete(GraphExtractLog).where(
        GraphExtractLog.user_id == user_id, GraphExtractLog.note_id == note_id))
    # 该笔记抽取时创建的关系（按溯源列）
    await db.execute(delete(GraphRelation).where(
        GraphRelation.user_id == user_id, GraphRelation.source_note_id == note_id))
    await _sweep_orphans(db, user_id, candidate_ids, [note_id])


async def cleanup_doc_graph(user_id: str, doc_id: str) -> None:
    """知识库文档删除联动：清理该文档的节点元数据（MySQL 注册行 + Neo4j 图节点）、
    实体关联、Chunk、抽取日志、溯源关系，并清扫因该文档而成为孤儿的实体
    （独立会话，异常不外抛）。

    与 cleanup_note_graph 不同，文档删除走知识库服务（无 DB 会话），故自开 AsyncSessionLocal 会话。
    """
    try:
        async with AsyncSessionLocal() as db:
            store = get_graph_store(db)
            if isinstance(store, Neo4jGraphStore):
                candidates = await store.clear_source_data(user_id, "doc", doc_id)
                await store.sweep_orphan_entities(user_id, candidates, [doc_id])
                # MySQL 侧仅维护注册行与抽取日志
                await db.execute(delete(GraphDoc).where(
                    GraphDoc.user_id == user_id, GraphDoc.id == doc_id))
                await db.execute(delete(GraphExtractLog).where(
                    GraphExtractLog.user_id == user_id, GraphExtractLog.note_id == doc_id,
                    GraphExtractLog.source_type == "doc"))
                await db.commit()
                return

            # MySQL 回落路径（测试兼容；Task 9 随 Chroma 一并移除）
            candidate_ids = (await db.execute(
                select(GraphEntityNote.entity_id).where(
                    GraphEntityNote.user_id == user_id, GraphEntityNote.note_id == doc_id,
                    GraphEntityNote.source_type == "doc"))).scalars().all()
            await db.execute(delete(GraphDoc).where(
                GraphDoc.user_id == user_id, GraphDoc.id == doc_id))
            await db.execute(delete(GraphEntityNote).where(
                GraphEntityNote.user_id == user_id, GraphEntityNote.note_id == doc_id,
                GraphEntityNote.source_type == "doc"))
            await db.execute(delete(GraphExtractLog).where(
                GraphExtractLog.user_id == user_id, GraphExtractLog.note_id == doc_id,
                GraphExtractLog.source_type == "doc"))
            await db.execute(delete(GraphRelation).where(
                GraphRelation.user_id == user_id, GraphRelation.source_note_id == doc_id,
                GraphRelation.source_type == "doc"))
            await _sweep_orphans(db, user_id, candidate_ids, [doc_id])
            await db.commit()
    except Exception as e:
        logger.error(f"清理文档图谱失败 user_id={user_id} doc_id={doc_id}: {e}")


async def cleanup_doc_graph_by_filename(user_id: str, filename: str) -> None:
    """按文件名清理文档图谱（md5 记录缺失时删除路径的兜底，防残留）。"""
    try:
        async with AsyncSessionLocal() as db:
            doc = (await db.execute(select(GraphDoc).where(
                GraphDoc.user_id == user_id, GraphDoc.filename == filename))).scalar_one_or_none()
        if doc is not None:
            await cleanup_doc_graph(user_id, doc.id)
    except Exception as e:
        logger.error(f"按文件名清理文档图谱失败 user_id={user_id} filename={filename}: {e}")


async def cleanup_all_docs_graph(user_id: str) -> None:
    """清空用户全部文档的图谱数据（清空知识库时调用，独立会话，异常不外抛）。"""
    try:
        async with AsyncSessionLocal() as db:
            store = get_graph_store(db)
            if isinstance(store, Neo4jGraphStore):
                await store.clear_all_docs(user_id)
                await db.execute(delete(GraphDoc).where(GraphDoc.user_id == user_id))
                await db.execute(delete(GraphExtractLog).where(
                    GraphExtractLog.user_id == user_id, GraphExtractLog.source_type == "doc"))
                await db.commit()
                return

            # MySQL 回落路径（测试兼容；Task 9 随 Chroma 一并移除）
            candidate_ids = (await db.execute(
                select(GraphEntityNote.entity_id).where(
                    GraphEntityNote.user_id == user_id, GraphEntityNote.source_type == "doc"))).scalars().all()
            removed_doc_ids = (await db.execute(
                select(GraphEntityNote.note_id).where(
                    GraphEntityNote.user_id == user_id, GraphEntityNote.source_type == "doc").distinct())).scalars().all()
            await db.execute(delete(GraphDoc).where(GraphDoc.user_id == user_id))
            await db.execute(delete(GraphEntityNote).where(
                GraphEntityNote.user_id == user_id, GraphEntityNote.source_type == "doc"))
            await db.execute(delete(GraphExtractLog).where(
                GraphExtractLog.user_id == user_id, GraphExtractLog.source_type == "doc"))
            await db.execute(delete(GraphRelation).where(
                GraphRelation.user_id == user_id, GraphRelation.source_type == "doc"))
            await _sweep_orphans(db, user_id, candidate_ids, removed_doc_ids)
            await db.commit()
    except Exception as e:
        logger.error(f"清空用户文档图谱失败 user_id={user_id}: {e}")
