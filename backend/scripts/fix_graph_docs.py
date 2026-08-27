"""补齐回填时遗漏的 graph_docs 文档节点（幂等：哈希一致自动跳过抽取）。

已抽取的文档调用 maybe_schedule_doc_extraction 会写入 GraphDoc 行并因哈希一致跳过重抽。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import asyncio

from app.core.logger_handler import logger
from app.graph.services.graph_service import maybe_schedule_doc_extraction
from scripts.backfill_graph import list_docs, list_users


async def main():
    users = await list_users()
    for uid in users:
        docs = await list_docs(uid)
        for md5, filename, full_text in docs:
            triggered = await maybe_schedule_doc_extraction(uid, md5, filename, full_text)
            logger.info(f"[{uid}] 文档节点 {filename}: {'触发抽取' if triggered else '已存在，跳过'}")
    logger.info("graph_docs 补齐完成")


asyncio.run(main())