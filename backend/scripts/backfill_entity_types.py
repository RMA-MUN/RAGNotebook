"""为未分类实体回填 type_id（一次性数据修复脚本）。

背景：
  旧抽取逻辑在写入实体时丢弃了 LLM 返回的 type，导致 graph_entities.type_id 全为 NULL，
  类型筛选永远为空。由于 type 字段从未落库，无法直接推算旧类型，
  只能重新抽取来源、让 LLM 重新生成 type 并在写入时回填。

原理：
  - 找出所有 type_id IS NULL 的实体
  - 从 graph_entity_notes 定位这些实体关联的来源（笔记 UUID / 文档 md5，含 source_type）
  - 对去重后的来源逐个重新触发抽取（复用 _run_extraction 同款逻辑）
  - _run_extraction 会先清本来源旧关系再 upsert，LLM 重抽后将按语义类型回填 type_id；
    已分类实体不触碰（只遍历未分类实体关联的来源）

用法（在 backend 目录下执行）：
  .venv\\Scripts\\python.exe -m scripts.backfill_entity_types --dry-run   # 只统计，不触发抽取
  .venv\\Scripts\\python.exe -m scripts.backfill_entity_types --user <uid>  # 只处理指定用户
  .venv\\Scripts\\python.exe -m scripts.backfill_entity_types              # 全量回填
"""
import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select

from app.core.logger_handler import logger
from app.db.db_config import AsyncSessionLocal
from app.graph.services.graph_service import _run_extraction, content_hash
from app.models.graph import GraphEntity, GraphEntityNote
from app.models.note import Note
from app.models.user_model import User

FORCE_REEXTRACT_NOTE_TYPES = {"note"}
FORCE_REEXTRACT_DOC_TYPES = {"doc"}


async def _ensure_chat_model():
    """初始化 LLM（与后端启动路径一致）。"""
    from app.core.background_init import init_manager
    if init_manager.chat_model is not None:
        return
    from app.utils.factory import ChatModelFactory
    init_manager.chat_model = await asyncio.to_thread(lambda: ChatModelFactory().generator())
    logger.info("✅ chat_model 初始化完成")


async def list_users() -> list[str]:
    async with AsyncSessionLocal() as db:
        rows = (await db.execute(select(User.uuid))).scalars().all()
        return list(rows)


async def untyped_entity_sources(user_id: str) -> dict[str, list[tuple[str, str]]]:
    """返回 {source_type: set[(source_id, ...)]}，仅含未分类实体关联的来源。

    来源是从 graph_entity_notes 关联行反查（未分类实体 → 其来源 note_id/source_type）。
    """
    async with AsyncSessionLocal() as db:
        entity_ids = (await db.execute(
            select(GraphEntity.id).where(
                GraphEntity.user_id == user_id,
                GraphEntity.type_id.is_(None),
            )
        )).scalars().all()
        if not entity_ids:
            return {"note": [], "doc": []}
        rows = (await db.execute(
            select(GraphEntityNote.note_id, GraphEntityNote.source_type).where(
                GraphEntityNote.user_id == user_id,
                GraphEntityNote.entity_id.in_(entity_ids),
            ).distinct())).all()
    sources: dict[str, list[str]] = {"note": [], "doc": []}
    for note_id, source_type in rows:
        source_type = source_type or "note"
        if source_type not in sources:
            continue
        if note_id not in sources[source_type]:
            sources[source_type].append(note_id)
    return sources


async def _fetch_doc_content(md5: str) -> str:
    """按 md5 从 ChromaDB 取该文档全文（与 backfill_graph.list_docs 同参数）。"""
    from app.rag.vector_store import VectorStoreService
    store = VectorStoreService()
    all_docs = await asyncio.to_thread(
        store.vectors_store.get,
        include=["documents", "metadatas"],
        where={"md5": md5},
    )
    return "\n".join(all_docs["documents"])


async def _reextract_note(source_id: str, user_id: str) -> None:
    async with AsyncSessionLocal() as db:
        note = (await db.execute(select(Note).where(
            Note.id == source_id, Note.user_id == user_id))).scalar_one_or_none()
        if note is None:
            logger.warning(f"笔记不存在，跳过 source_id={source_id}")
            return
        body = note.content or ""
        body_hash = content_hash(body)
    await _run_extraction(source_id, user_id, note.title or "", body_hash,
                          body=body, source_type="note")


async def _reextract_doc(source_id: str, user_id: str) -> None:
    from app.models.graph import GraphDoc
    async with AsyncSessionLocal() as db:
        doc = (await db.execute(select(GraphDoc).where(
            GraphDoc.id == source_id, GraphDoc.user_id == user_id))).scalar_one_or_none()
    if doc is None:
        logger.warning(f"文档元数据不存在，跳过 source_id={source_id}")
        return
    content = await _fetch_doc_content(source_id)
    if not content:
        logger.warning(f"文档正文为空，跳过 source_id={source_id}")
        return
    body_hash = content_hash(content)
    await _run_extraction(source_id, user_id, doc.filename, body_hash,
                          body=content, source_type="doc")


async def backfill_user(user_id: str, sem: asyncio.Semaphore, stats: dict) -> None:
    sources = await untyped_entity_sources(user_id)
    note_ids = sources.get("note", [])
    doc_ids = sources.get("doc", [])
    logger.info(f"用户 {user_id}: 未分类实体关联来源 笔记 {len(note_ids)} 篇 / 文档 {len(doc_ids)} 个")

    tasks: list[asyncio.Task] = []

    async def run_note(source_id: str):
        stats["trigger_note"] += 1
        async with sem:
            await _reextract_note(source_id, user_id)
        stats["done_note"] += 1

    async def run_doc(source_id: str):
        stats["trigger_doc"] += 1
        async with sem:
            await _reextract_doc(source_id, user_id)
        stats["done_doc"] += 1

    for nid in note_ids:
        tasks.append(asyncio.create_task(run_note(nid)))
    for did in doc_ids:
        tasks.append(asyncio.create_task(run_doc(did)))

    if tasks:
        await asyncio.gather(*tasks)


async def main() -> None:
    parser = argparse.ArgumentParser(description="为未分类实体回填 type_id")
    parser.add_argument("--dry-run", action="store_true", help="只统计数量，不触发抽取")
    parser.add_argument("--user", default=None, help="只处理指定用户（默认全部用户）")
    args = parser.parse_args()

    users = [args.user] if args.user else await list_users()
    if not users:
        logger.error("没有找到任何用户")
        return

    total_notes = 0
    total_docs = 0
    for uid in users:
        sources = await untyped_entity_sources(uid)
        notes = sources.get("note", [])
        docs = sources.get("doc", [])
        total_notes += len(notes)
        total_docs += len(docs)
        logger.info(f"[{uid}] 未分类实体关联来源 笔记 {len(notes)} 篇 / 文档 {len(docs)} 个")

    logger.info(f"合计：用户 {len(users)} 个 / 笔记 {total_notes} 篇 / 文档 {total_docs} 个")
    if args.dry_run:
        logger.info("dry-run 模式：不触发抽取，请移除 --dry-run 后执行真实回填")
        return

    await _ensure_chat_model()
    sem = asyncio.Semaphore(4)  # 限制 LLM 并发，避免限流
    stats = {"trigger_note": 0, "done_note": 0, "trigger_doc": 0, "done_doc": 0}
    for uid in users:
        try:
            await backfill_user(uid, sem, stats)
        except Exception as e:
            logger.error(f"用户 {uid} 回填失败: {e}", exc_info=True)
    logger.info(f"回填完成：笔记 触发 {stats['trigger_note']} / 完成 {stats['done_note']}"
                f"；文档 触发 {stats['trigger_doc']} / 完成 {stats['done_doc']}")


if __name__ == "__main__":
    asyncio.run(main())
