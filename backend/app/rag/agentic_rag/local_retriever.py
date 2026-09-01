"""本地检索器：全部走 Neo4j 图存储（种子 = Chunk 向量+全文，图扩展 = 实体/关系/提及 chunk）。

- search_notes          → search_chunks(kinds=["note"])
- search_knowledge_base → search_chunks(kinds=["doc"])
- hybrid_search         → search_chunks(kinds=None)（单次调用，RRF 已融合笔记与文档）
- search_graph          → 实体候选词匹配 → 实体证据 + 命中实体的 chunk 片段补充

工具名保持不变，执行层为图存储检索。
"""
import asyncio
from collections.abc import Callable
from typing import Any

from app.core.logger_handler import logger
from app.db.db_config import AsyncSessionLocal
from app.graph.schemas.graph import ChunkHit
from app.graph.storage import get_graph_store
from app.graph.storage.neo4j_graph_store import Neo4jGraphStore
from app.rag.agentic_rag.query_entity_extractor import QueryEntityExtractor
from app.rag.agentic_rag.schemas import Evidence, RetrievalStep


class LocalRetriever:
    def __init__(
        self,
        note_service: Any | None = None,
        session_factory: Callable[[], Any] = AsyncSessionLocal,
        query_entity_extractor: QueryEntityExtractor | None = None,
        embed_model: Any | None = None,
    ):
        self.note_service = note_service
        self.session_factory = session_factory
        self.query_entity_extractor = query_entity_extractor
        self.embed_model = embed_model

    async def search(self, user_id: str, steps: list[RetrievalStep]) -> list[Evidence]:
        evidences: list[Evidence] = []

        for step in steps:
            try:
                if step.tool == "search_notes":
                    evidences.extend(await self._search_chunks(user_id, step, kinds=["note"]))
                elif step.tool == "search_knowledge_base":
                    evidences.extend(await self._search_chunks(user_id, step, kinds=["doc"]))
                elif step.tool == "search_graph":
                    evidences.extend(await self._search_graph(user_id, step))
                elif step.tool == "hybrid_search":
                    evidences.extend(await self._search_chunks(user_id, step, kinds=None))
            except Exception as e:
                # 单步检索失败（含 Neo4j 不可用）不阻塞其余步骤：聊天主流程功能降级
                logger.warning(f"检索步骤 {step.tool} 失败，已跳过: {e}")

        return evidences

    async def _search_chunks(self, user_id: str, step: RetrievalStep,
                             kinds: list[str] | None) -> list[Evidence]:
        """种子检索：向量 + 全文（store 内 RRF 融合）取 top chunk。"""
        async with self.session_factory() as db:
            store = get_graph_store(db)
            embedding = await self._query_embedding(step.query)
            hits = await store.search_chunks(user_id, embedding, step.query, kinds, step.top_k)
            return [self._chunk_to_evidence(hit) for hit in hits]

    async def _search_graph(self, user_id: str, step: RetrievalStep) -> list[Evidence]:
        """从知识图谱检索：先抽实体候选词匹配实体（证据=描述+类型+别名+关联来源），
        Neo4j 主路径再补命中实体所在的 chunk 片段，让图谱证据有正文可引。"""
        evidences: list[Evidence] = []
        names = await self._entity_candidates(step.query)
        matched_entity_ids: list[str] = []
        async with self.session_factory() as db:
            store = get_graph_store(db)
            seen_entity_ids: set[str] = set()
            for name in names:
                entities = await store.search_entities(user_id, name, limit=step.top_k)
                for entity in entities:
                    if entity.id in seen_entity_ids:
                        continue
                    seen_entity_ids.add(entity.id)
                    matched_entity_ids.append(entity.id)
                    links = await store.get_entity_notes(user_id, entity.id)
                    noted: set[str] = set()
                    notes: list[str] = []
                    for link in links:
                        label = link.source_name or link.note_id
                        if label in noted:
                            continue
                        noted.add(label)
                        notes.append(label)
                    title = entity.display_name or entity.name
                    content = entity.description or ""
                    if content:
                        content = f"{content}（类型：{entity.type_id or '未分类'}；别名：{', '.join(entity.aliases) if entity.aliases else '—'}）"
                    if notes:
                        content = f"{content}\n关联来源：{'、'.join(notes)}" if content else "关联来源：" + "、".join(notes)
                    evidences.append(Evidence(
                        id=entity.id,
                        source="graph",
                        title=title,
                        content=content or title,
                        metadata={"type_id": entity.type_id, "aliases": entity.aliases},
                    ))

            if isinstance(store, Neo4jGraphStore) and matched_entity_ids:
                try:
                    chunk_hits = await store.get_chunks_mentioning(
                        user_id, matched_entity_ids, limit=6)
                except NotImplementedError:
                    chunk_hits = []
                evidences.extend(self._chunk_to_evidence(hit) for hit in chunk_hits)
        return evidences

    async def _entity_candidates(self, query: str) -> list[str]:
        """从问句抽实体候选词。优先 LLM，失败回落规则拆词。"""
        extractor = self.query_entity_extractor or QueryEntityExtractor()
        try:
            return await extractor.extract(query)
        except Exception as e:
            from app.core.logger_handler import logger

            logger.warning(f"查询实体抽取失败，回落规则: {query}: {e}")
            return QueryEntityExtractor._fallback_candidates(query)

    async def _query_embedding(self, query: str) -> list[float] | None:
        """query 向量实时计算（失败返回 None，退化为纯全文检索）。"""
        model = self.embed_model
        if model is None:
            from app.core.background_init import init_manager

            model = init_manager.embed_model
        if model is None or not query:
            return None
        try:
            return await asyncio.to_thread(model.embed_query, query)
        except Exception:
            return None

    @staticmethod
    def _chunk_to_evidence(hit: ChunkHit) -> Evidence:
        return Evidence(
            id=hit.id,
            source="note" if hit.kind == "note" else "knowledge_base",
            title=hit.source_name or ("笔记" if hit.kind == "note" else "知识库文档"),
            content=hit.text,
            score=hit.score,
            metadata={"source_id": hit.source_id, "chunk_index": hit.chunk_index, **hit.metadata},
        )
