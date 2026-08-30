"""图谱 API 路由。"""
import asyncio
import functools
import json

from fastapi import Depends, HTTPException
from fastapi.responses import StreamingResponse
from fastapi.routing import APIRouter

from app.core.logger_handler import logger
from app.graph.services.event_bus import event_bus
from app.graph.storage.neo4j_client import GraphUnavailableError
from app.utils.auth_utils import get_current_user_id

graph_router = APIRouter(prefix="/api/graph", tags=["graph"])


from fastapi import Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.success_response import success_response
from app.db.db_config import get_db
from app.graph.schemas.graph import EntityIn, MergeRequest, RelationIn, TypeIn
from app.graph.services.graph_service import manual_re_extract
from app.graph.storage import get_graph_store
from app.models.note import Note


def _graph_gate(fn):
    """图谱端点统一降级：Neo4j 未配置或连接不可用时返回 503，不影响笔记/聊天/知识库主流程。"""
    @functools.wraps(fn)
    async def wrapper(*args, **kwargs):
        try:
            return await fn(*args, **kwargs)
        except GraphUnavailableError as e:
            logger.warning(f"图谱服务不可用（未配置）: {e}")
            raise HTTPException(status_code=503, detail="图谱服务不可用：未配置 Neo4j") from e
        except Exception as e:
            from neo4j.exceptions import Neo4jError

            if isinstance(e, Neo4jError):
                logger.error(f"图谱服务不可用（Neo4j 故障）: {e}", exc_info=True)
                raise HTTPException(status_code=503, detail="图谱服务不可用，请稍后重试") from e
            raise
    return wrapper


@graph_router.get("/overview")
@_graph_gate
async def overview(user_id: str = Depends(get_current_user_id),
                   db: AsyncSession = Depends(get_db),
                   types: str | None = Query(None),
                   limit: int = Query(50, ge=1, le=200)):
    store = get_graph_store(db)
    type_ids = types.split(",") if types else None
    view = await store.get_overview(user_id, type_ids, limit)
    return success_response(data=view.model_dump())


@graph_router.get("/entity/{entity_id}")
@_graph_gate
async def get_entity(entity_id: str, user_id: str = Depends(get_current_user_id),
                     db: AsyncSession = Depends(get_db)):
    store = get_graph_store(db)
    e = await store.get_entity(user_id, entity_id)
    if not e:
        return success_response(data=None, message="实体不存在")
    return success_response(data=e.model_dump())


@graph_router.get("/entity/{entity_id}/neighbors")
@_graph_gate
async def neighbors(entity_id: str, depth: int = Query(1, ge=1, le=3),
                    user_id: str = Depends(get_current_user_id),
                    db: AsyncSession = Depends(get_db)):
    view = await get_graph_store(db).get_neighbors(user_id, entity_id, depth)
    return success_response(data=view.model_dump())


@graph_router.get("/entity/{entity_id}/notes")
@_graph_gate
async def entity_notes(entity_id: str, user_id: str = Depends(get_current_user_id),
                       db: AsyncSession = Depends(get_db)):
    links = await get_graph_store(db).get_entity_notes(user_id, entity_id)
    return success_response(data=[l.model_dump() for l in links])


@graph_router.get("/notes/{note_id}/related")
@_graph_gate
async def note_related(note_id: str, user_id: str = Depends(get_current_user_id),
                       db: AsyncSession = Depends(get_db)):
    view = await get_graph_store(db).get_note_graph(user_id, note_id)
    return success_response(data=view.model_dump())


@graph_router.get("/docs/{doc_id}/related")
@_graph_gate
async def doc_related(doc_id: str, user_id: str = Depends(get_current_user_id),
                      db: AsyncSession = Depends(get_db)):
    view = await get_graph_store(db).get_doc_graph(user_id, doc_id)
    return success_response(data=view.model_dump())


@graph_router.get("/search")
@_graph_gate
async def search(q: str = Query(..., min_length=1),
                 user_id: str = Depends(get_current_user_id),
                 db: AsyncSession = Depends(get_db)):
    like = f"%{q}%"
    entities = await get_graph_store(db).search_entities(user_id, q, 10)
    notes = (await db.execute(
        select(Note).where(Note.user_id == user_id, Note.title.like(like)).limit(10))).scalars().all()
    return success_response(data={
        "entities": [{"id": e.id, "name": e.display_name or e.name, "type_id": e.type_id} for e in entities],
        "notes": [{"id": n.id, "title": n.title} for n in notes],
    })


@graph_router.get("/extract-logs")
async def extract_logs(note_id: str | None = Query(None),
                       user_id: str = Depends(get_current_user_id),
                       db: AsyncSession = Depends(get_db)):
    from app.models.graph import GraphExtractLog
    stmt = select(GraphExtractLog).where(GraphExtractLog.user_id == user_id)
    if note_id:
        stmt = stmt.where(GraphExtractLog.note_id == note_id)
    rows = (await db.execute(stmt.order_by(GraphExtractLog.triggered_at.desc()).limit(50))).scalars().all()
    return success_response(data=[{
        "note_id": r.note_id, "source_type": r.source_type, "content_hash": r.content_hash,
        "status": r.status, "new_count": r.new_count, "update_count": r.update_count,
        "error_message": r.error_message,
    } for r in rows])


@graph_router.post("/entities")
@_graph_gate
async def create_entity(payload: EntityIn, user_id: str = Depends(get_current_user_id),
                        db: AsyncSession = Depends(get_db)):
    e = await get_graph_store(db).upsert_entity(user_id, payload)
    await db.commit()
    return success_response(data=e.model_dump())


@graph_router.put("/entities/{entity_id}")
@_graph_gate
async def update_entity(entity_id: str, payload: EntityIn,
                        user_id: str = Depends(get_current_user_id),
                        db: AsyncSession = Depends(get_db)):
    store = get_graph_store(db)
    e = await store.get_entity(user_id, entity_id)
    if not e:
        return success_response(data=None, message="实体不存在")
    updates = payload.model_dump(exclude_unset=True)
    merged = EntityIn(
        name=updates.get("name", e.name),
        display_name=updates.get("display_name", e.display_name),
        type_id=updates.get("type_id", e.type_id),
        description=updates.get("description", e.description),
        aliases=updates.get("aliases", e.aliases),
        confidence=updates.get("confidence", e.confidence),
        source_note_ids=e.source_note_ids,
    )
    # 按 id 整体更新：upsert_entity 按名称去重，改名会创建新实体而非修改
    try:
        updated = await store.update_entity(user_id, entity_id, merged)
    except ValueError as e:
        await db.rollback()
        raise HTTPException(status_code=409, detail=str(e))
    await db.commit()
    return success_response(data=updated.model_dump())


@graph_router.delete("/entities/{entity_id}")
@_graph_gate
async def delete_entity(entity_id: str, user_id: str = Depends(get_current_user_id),
                        db: AsyncSession = Depends(get_db)):
    await get_graph_store(db).delete_entity(user_id, entity_id)
    await db.commit()
    return success_response(message="实体已删除")


@graph_router.post("/entities/merge")
@_graph_gate
async def merge_entities(payload: MergeRequest, user_id: str = Depends(get_current_user_id),
                         db: AsyncSession = Depends(get_db)):
    # 自合并守卫：target_id == source_id 时直接返回，避免 merge_entities 删行后误删实体
    if payload.target_id == payload.source_id:
        e = await get_graph_store(db).get_entity(user_id, payload.target_id)
        return success_response(data=e.model_dump() if e else None, message="合并目标与源相同")
    e = await get_graph_store(db).merge_entities(user_id, payload.target_id, payload.source_id)
    await db.commit()
    return success_response(data=e.model_dump())


@graph_router.get("/types")
@_graph_gate
async def list_types(user_id: str = Depends(get_current_user_id),
                     db: AsyncSession = Depends(get_db)):
    types = await get_graph_store(db).list_types(user_id)
    return success_response(data=[t.model_dump() for t in types])


@graph_router.post("/types")
@_graph_gate
async def create_type(payload: TypeIn, user_id: str = Depends(get_current_user_id),
                      db: AsyncSession = Depends(get_db)):
    t = await get_graph_store(db).upsert_type(user_id, payload)
    await db.commit()
    return success_response(data=t.model_dump())


@graph_router.put("/types/{type_id}")
@_graph_gate
async def update_type(type_id: str, payload: TypeIn,
                      user_id: str = Depends(get_current_user_id),
                      db: AsyncSession = Depends(get_db)):
    t = await get_graph_store(db).upsert_type(user_id, payload)
    await db.commit()
    return success_response(data=t.model_dump())


@graph_router.delete("/types/{type_id}")
@_graph_gate
async def delete_type(type_id: str, user_id: str = Depends(get_current_user_id),
                      db: AsyncSession = Depends(get_db)):
    await get_graph_store(db).delete_type(user_id, type_id)
    await db.commit()
    return success_response(message="类型已删除")


@graph_router.post("/relations")
@_graph_gate
async def create_relation(payload: RelationIn, user_id: str = Depends(get_current_user_id),
                          db: AsyncSession = Depends(get_db)):
    try:
        r = await get_graph_store(db).create_relation(user_id, payload)
    except ValueError as e:
        await db.rollback()
        raise HTTPException(status_code=404, detail=str(e))
    await db.commit()
    return success_response(data=r.model_dump())


@graph_router.delete("/relations/{relation_id}")
@_graph_gate
async def delete_relation(relation_id: str, user_id: str = Depends(get_current_user_id),
                          db: AsyncSession = Depends(get_db)):
    await get_graph_store(db).delete_relation(user_id, relation_id)
    await db.commit()
    return success_response(message="关系已删除")


@graph_router.post("/notes/{note_id}/re-extract")
async def re_extract(note_id: str, user_id: str = Depends(get_current_user_id)):
    ok = await manual_re_extract(note_id, user_id)
    return success_response(data={"triggered": ok}, message="已重新抽取" if ok else "抽取进行中")


@graph_router.get("/events")
async def graph_events(user_id: str = Depends(get_current_user_id)):
    """SSE 长连接订阅：抽取进度/结果实时推送（fetch ReadableStream 消费，带 JWT）。"""
    q = await event_bus.subscribe(user_id)

    async def gen():
        try:
            yield "retry: 3000\n\n"
            while True:
                try:
                    event = await asyncio.wait_for(q.get(), timeout=15.0)
                    yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                except asyncio.TimeoutError:
                    yield "data: {\"type\":\"ping\"}\n\n"
        finally:
            await event_bus.unsubscribe(user_id, q)

    return StreamingResponse(gen(), media_type="text/event-stream")