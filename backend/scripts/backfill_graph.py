"""已有笔记与知识库文档回填 Neo4j 知识图谱（全量重建脚本）。

用法（在 backend 目录下执行）：
  uv run python -m scripts.backfill_graph --dry-run            # 只统计，不触发抽取
  uv run python -m scripts.backfill_graph --user <uid>         # 只处理指定用户
  uv run python -m scripts.backfill_graph                      # 增量回填（内容哈希幂等，可重复执行）
  uv run python -m scripts.backfill_graph --force              # 全部重抽（忽略已有抽取日志哈希）
  uv run python -m scripts.backfill_graph --wipe               # 先清空 Neo4j 业务数据/抽取日志/任务表，再全量重抽
  uv run python -m scripts.backfill_graph --enqueue-only       # 只写任务表，由应用内 worker 消费

原理：
  - 笔记：遍历 MySQL note 表 → 任务 payload {"text": 正文}
  - 知识库文档：直连 ChromaDB 读该用户切片（保留 page/图片元数据，与应用代码解耦）→ payload {"chunks": [...]}
  - 抽取/建边/Chunk 写入复用 graph_service.process_task 同款管线（LLM 抽取 → Neo4j 实体/关系/
    Chunk/MENTIONS 规则匹配）
"""
import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import chromadb
from sqlalchemy import delete as sa_delete
from sqlalchemy import select

from app.core.logger_handler import logger
from app.db.db_config import AsyncSessionLocal, init_db
from app.graph.services.graph_service import _enqueue_build_task, content_hash
from app.models.graph import GraphBuildTask, GraphExtractLog
from app.models.note import Note
from app.models.user_model import User
from app.utils.config import chroma_config


async def _ensure_models():
    """初始化 LLM 与嵌入模型（与后端启动路径一致）。"""
    from app.core.background_init import init_manager
    from app.utils.factory import ChatModelFactory, EmbedModelFactory

    if init_manager.chat_model is None:
        init_manager.chat_model = await asyncio.to_thread(lambda: ChatModelFactory().generator())
        logger.info("✅ chat_model 初始化完成")
    if init_manager.embed_model is None:
        init_manager.embed_model = await asyncio.to_thread(lambda: EmbedModelFactory().generator())
        logger.info("✅ embed_model 初始化完成")


async def _ensure_schema():
    """确保 Neo4j 约束/索引就绪（幂等；向量维度探针自动测定）。"""
    from app.core.background_init import init_manager
    from app.graph.storage.neo4j_client import ensure_graph_schema, neo4j_configured

    if not neo4j_configured():
        raise RuntimeError("NEO4J_URI 未配置，无法回填")
    await ensure_graph_schema(init_manager.embed_model)


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


async def list_docs(user_id: str) -> list[tuple[str, str, list[dict]]]:
    """直连 ChromaDB 读该用户全部切片，按 md5 分组。

    返回 [(md5, filename, chunks)]，chunks 为 [{"chunk_index", "text", "page", "image_paths"}]。
    注意：Chroma 不保证切片写入顺序，此处按 page 升序尽量还原阅读顺序。
    """
    persist_dir = chroma_config["persist_directory"]
    client = chromadb.PersistentClient(path=str(Path(persist_dir).resolve()))
    collection = client.get_collection(chroma_config["collection_name"])
    all_docs = collection.get(include=["documents", "metadatas"], where={"user_id": user_id})

    groups: dict[str, dict] = {}
    for i, content in enumerate(all_docs["documents"] or []):
        meta = all_docs["metadatas"][i] if i < len(all_docs["metadatas"]) else {}
        md5 = meta.get("md5")
        if not md5:
            continue
        g = groups.setdefault(md5, {"filename": meta.get("original_filename") or meta.get("filename") or md5,
                                    "chunks": []})
        g["chunks"].append({
            "text": content or "",
            "page": meta.get("page"),
            "image_paths": meta.get("image_paths") or [],
        })
    result = []
    for md5, g in groups.items():
        g["chunks"].sort(key=lambda c: (c.get("page") is None, c.get("page") or 0))
        chunks = [{"chunk_index": i, **c} for i, c in enumerate(g["chunks"])]
        result.append((md5, g["filename"], chunks))
    return result


async def wipe_all(users: list[str]) -> None:
    """清空 Neo4j 业务数据 + MySQL 抽取日志/任务表（--wipe 模式）。"""
    from app.graph.storage.neo4j_client import get_neo4j_driver

    driver = get_neo4j_driver()
    # 全量清空业务节点与关系（不动数据库角色/索引）
    await driver.execute_query(
        "MATCH (n) WHERE n:Chunk OR n:Entity OR n:EntityType OR n:Note OR n:Doc "
        "DETACH DELETE n")
    logger.info("✅ Neo4j 业务数据已清空")
    async with AsyncSessionLocal() as db:
        await db.execute(sa_delete(GraphExtractLog))
        await db.execute(sa_delete(GraphBuildTask))
        await db.commit()
    logger.info("✅ MySQL 抽取日志/构建任务已清空")


async def enqueue_user(user_id: str, force: bool, stats: dict) -> int:
    """把用户的笔记与文档全部写入构建任务表，返回任务数。"""
    count = 0
    for note_id, title, content in await list_notes(user_id):
        await _enqueue_build_task(user_id, "note", note_id, title,
                                  content_hash(content), {"text": content}, force=force)
        count += 1
    for md5, filename, chunks in await list_docs(user_id):
        full_text = "\n".join(c["text"] for c in chunks)
        await _enqueue_build_task(user_id, "doc", md5, filename,
                                  content_hash(full_text), {"chunks": chunks}, force=force)
        count += 1
    stats[f"enqueue:{user_id}"] = count
    return count


async def run_user_inline(user_id: str, sem: asyncio.Semaphore, stats: dict) -> None:
    """入队后立即在脚本内消费（不经 worker），限制 LLM 并发。"""
    from app.graph.services.graph_worker import _tick

    await enqueue_user(user_id, force=False, stats=stats)  # inline 模式仅增量
    while True:
        async with AsyncSessionLocal() as db:
            from sqlalchemy import func
            pending = (await db.execute(
                select(func.count()).select_from(GraphBuildTask)
                .where(GraphBuildTask.user_id == user_id,
                       GraphBuildTask.status == "pending"))).scalar_one()
        if pending == 0:
            break
        async with sem:
            await _tick()
        stats["processed"] += 1


async def main() -> None:
    parser = argparse.ArgumentParser(description="已有笔记/知识库文档回填 Neo4j 知识图谱")
    parser.add_argument("--dry-run", action="store_true", help="只统计数量，不触发抽取")
    parser.add_argument("--user", default=None, help="只处理指定用户（默认全部用户）")
    parser.add_argument("--force", action="store_true", help="全部重抽（跳过内容哈希判重）")
    parser.add_argument("--wipe", action="store_true", help="先清空 Neo4j 业务数据/日志/任务表，再全量重抽（含 force）")
    parser.add_argument("--enqueue-only", action="store_true", help="只写任务表，由应用内 worker 消费")
    args = parser.parse_args()

    force = args.force or args.wipe

    # 幂等建表/补列：脚本可独立于后端运行（graph_build_tasks 等新表在此创建）
    await init_db()
    logger.info("✅ 数据库表结构已就绪")

    users = [args.user] if args.user else await list_users()
    if not users:
        logger.error("没有找到任何用户")
        return

    total_notes = 0
    total_docs = 0
    for uid in users:
        total_notes += len(await list_notes(uid))
        total_docs += len(await list_docs(uid))
    logger.info(f"合计：用户 {len(users)} 个 / 笔记 {total_notes} 篇 / 知识库文档 {total_docs} 个")
    if args.dry_run:
        logger.info("dry-run 模式：不触发抽取，请移除 --dry-run 后执行真实回填")
        return

    if args.wipe:
        await wipe_all(users)
    await _ensure_models()
    await _ensure_schema()

    stats: dict = {"processed": 0}
    if args.enqueue_only:
        for uid in users:
            n = await enqueue_user(uid, force=force, stats=stats)
            logger.info(f"[{uid}] 已入队 {n} 个任务（等待应用内 worker 消费）")
        logger.info("入队完成。启动后端后 worker 将自动消费（任务持久化，重启可恢复）。")
        return

    sem = asyncio.Semaphore(4)  # 限制 LLM 并发，避免限流
    for uid in users:
        try:
            await run_user_inline(uid, sem, stats)
        except Exception as e:
            logger.error(f"用户 {uid} 回填失败: {e}", exc_info=True)
    logger.info(f"回填完成：共处理 {stats['processed']} 个任务")


if __name__ == "__main__":
    asyncio.run(main())
