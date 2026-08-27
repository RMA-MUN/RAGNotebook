"""已有笔记与知识库文档回填知识图谱（一次性数据迁移脚本）。

用法（在 backend 目录下执行）：
  .venv\\Scripts\\python.exe -m scripts.backfill_graph --dry-run   # 只统计，不触发抽取
  .venv\\Scripts\\python.exe -m scripts.backfill_graph --user <uid>  # 只处理指定用户
  .venv\\Scripts\\python.exe -m scripts.backfill_graph              # 全量回填

原理：
  - 笔记：遍历 MySQL note 表，复用 maybe_schedule_extraction 同款逻辑（内容哈希幂等）
  - 知识库文档：读 ChromaDB 该用户的全部切片，按 md5 分组拼全文，
    复用 maybe_schedule_doc_extraction 同款逻辑（幂等 + 建 graph_docs 文档节点）
  - 已抽取过且内容未变的会自动跳过（哈希一致）；脚本本身可安全重复执行
"""
import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select

from app.core.logger_handler import logger
from app.db.db_config import AsyncSessionLocal
from app.graph.services.graph_service import (
    _ensure_extract_log,
    _run_extraction,
    content_hash,
)
from app.models.note import Note
from app.models.user_model import User


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


async def list_notes(user_id: str) -> list[tuple[str, str, str]]:
    """返回 [(note_id, title, content)]"""
    async with AsyncSessionLocal() as db:
        rows = (await db.execute(
            select(Note.id, Note.title, Note.content).where(Note.user_id == user_id)
        )).all()
        return [(r[0], r[1] or "", r[2] or "") for r in rows]


async def list_docs(user_id: str) -> list[tuple[str, str, str]]:
    """读 ChromaDB 该用户全部切片，按 md5 分组拼全文。返回 [(md5, filename, full_text)]"""
    from app.rag.vector_store import VectorStoreService
    store = VectorStoreService()
    all_docs = await asyncio.to_thread(
        store.vectors_store.get,
        include=["documents", "metadatas"],
        where={"user_id": user_id},
    )
    groups: dict[str, dict] = {}
    for i, doc_id in enumerate(all_docs["ids"]):
        meta = all_docs["metadatas"][i] if i < len(all_docs["metadatas"]) else {}
        content = all_docs["documents"][i] if i < len(all_docs["documents"]) else ""
        md5 = meta.get("md5")
        if not md5:
            continue
        g = groups.setdefault(md5, {"filename": meta.get("original_filename") or meta.get("filename") or md5,
                                    "chunks": []})
        g["chunks"].append(content)
    return [(md5, g["filename"], "\n".join(g["chunks"])) for md5, g in groups.items()]


async def backfill_user(user_id: str, sem: asyncio.Semaphore, stats: dict) -> None:
    tasks: list[asyncio.Task] = []

    async def schedule(source_id: str, title: str, content: str, source_type: str):
        body_hash = content_hash(content)
        async with AsyncSessionLocal() as db:
            proceed = await _ensure_extract_log(db, user_id, source_id, body_hash, source_type=source_type)
        if not proceed:
            stats[f"skip_{source_type}"] += 1
            return
        stats[f"trigger_{source_type}"] += 1
        async with sem:
            await _run_extraction(source_id, user_id, title, body_hash, body=content, source_type=source_type)
        stats[f"done_{source_type}"] += 1

    notes = await list_notes(user_id)
    logger.info(f"用户 {user_id}: 笔记 {len(notes)} 篇")
    for note_id, title, content in notes:
        tasks.append(asyncio.create_task(schedule(note_id, title, content, "note")))

    docs = await list_docs(user_id)
    logger.info(f"用户 {user_id}: 知识库文档 {len(docs)} 个（按 md5 分组）")
    for md5, filename, full_text in docs:
        tasks.append(asyncio.create_task(schedule(md5, filename, full_text, "doc")))

    if tasks:
        await asyncio.gather(*tasks)


async def main() -> None:
    parser = argparse.ArgumentParser(description="已有笔记/知识库文档回填知识图谱")
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
        notes = await list_notes(uid)
        docs = await list_docs(uid)
        total_notes += len(notes)
        total_docs += len(docs)
        logger.info(f"[{uid}] 笔记 {len(notes)} 篇 / 知识库文档 {len(docs)} 个")

    logger.info(f"合计：用户 {len(users)} 个 / 笔记 {total_notes} 篇 / 知识库文档 {total_docs} 个")
    if args.dry_run:
        logger.info("dry-run 模式：不触发抽取，请移除 --dry-run 后执行真实回填")
        return

    await _ensure_chat_model()
    sem = asyncio.Semaphore(4)  # 限制 LLM 并发，避免限流
    stats = {"trigger_note": 0, "skip_note": 0, "done_note": 0,
             "trigger_doc": 0, "skip_doc": 0, "done_doc": 0}
    for uid in users:
        try:
            await backfill_user(uid, sem, stats)
        except Exception as e:
            logger.error(f"用户 {uid} 回填失败: {e}", exc_info=True)
    logger.info(f"回填完成：笔记 触发 {stats['trigger_note']} / 跳过 {stats['skip_note']} / 完成 {stats['done_note']}"
                f"；文档 触发 {stats['trigger_doc']} / 跳过 {stats['skip_doc']} / 完成 {stats['done_doc']}")


if __name__ == "__main__":
    asyncio.run(main())