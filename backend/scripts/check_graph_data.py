"""校验回填结果：统计图谱各表数据。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import asyncio
from sqlalchemy import func, select

from app.db.db_config import AsyncSessionLocal
from app.models.graph import (
    GraphDoc,
    GraphEntity,
    GraphEntityNote,
    GraphExtractLog,
    GraphNoteEdge,
    GraphRelation,
)


async def main():
    async with AsyncSessionLocal() as db:
        print("=== 图谱数据统计 ===")
        for label, model, cols in [
            ("graph_entities", GraphEntity, []),
            ("graph_relations", GraphRelation, []),
            ("graph_entity_notes", GraphEntityNote, ["source_type"]),
            ("graph_note_edges", GraphNoteEdge, []),
            ("graph_extract_logs", GraphExtractLog, ["source_type", "status"]),
            ("graph_docs", GraphDoc, []),
        ]:
            total = (await db.execute(select(func.count()).select_from(model))).scalar_one()
            print(f"{label}: {total}")
            for col in cols:
                rows = (await db.execute(
                    select(getattr(model, col), func.count()).group_by(getattr(model, col))
                )).all()
                if rows:
                    print(f"  按 {col}: " + ", ".join(f"{k}={v}" for k, v in rows))
        print("\n=== 文档节点 ===")
        docs = (await db.execute(select(GraphDoc.id, GraphDoc.filename))).all()
        for d in docs:
            print(f"  {d[1]} ({d[0]})")
        print("\n=== 抽取日志状态 ===")
        logs = (await db.execute(
            select(GraphExtractLog.source_type, GraphExtractLog.status, GraphExtractLog.new_count,
                   GraphExtractLog.error_message).order_by(GraphExtractLog.triggered_at)
        )).all()
        for l in logs:
            print(f"  [{l[0]}] {l[1]} new={l[2]} err={l[3]}")


asyncio.run(main())