import hashlib
from collections.abc import Callable
from typing import Any

from langchain_core.documents import Document

from app.db.db_config import AsyncSessionLocal
from app.rag.agentic_rag.schemas import Evidence, RetrievalStep
from app.rag.vector_store import VectorStoreService


class LocalRetriever:
    def __init__(
        self,
        note_service: Any | None = None,
        vector_store: Any | None = None,
        session_factory: Callable[[], Any] = AsyncSessionLocal,
    ):
        self.note_service = note_service
        self.vector_store = vector_store
        self.session_factory = session_factory

    async def search(self, user_id: str, steps: list[RetrievalStep]) -> list[Evidence]:
        evidences: list[Evidence] = []

        for step in steps:
            if step.tool == "search_notes":
                evidences.extend(await self._search_notes(user_id, step))
            elif step.tool == "search_knowledge_base":
                evidences.extend(await self._search_knowledge_base(user_id, step))
            elif step.tool == "hybrid_search":
                evidences.extend(await self._search_notes(user_id, step))
                evidences.extend(await self._search_knowledge_base(user_id, step))

        return evidences

    async def _search_notes(self, user_id: str, step: RetrievalStep) -> list[Evidence]:
        note_service = self._note_service()
        if note_service is None:
            return []

        async with self.session_factory() as db:
            notes = await note_service.search_notes(db, user_id, step.query, top_k=step.top_k)

        return [self._note_to_evidence(note) for note in notes]

    async def _search_knowledge_base(self, user_id: str, step: RetrievalStep) -> list[Evidence]:
        vector_store = self.vector_store or VectorStoreService()
        retriever = await vector_store.get_retriever(step.query, user_id)
        documents = (await retriever.ainvoke(step.query))[: step.top_k]
        return [self._document_to_evidence(document) for document in documents]

    def _note_service(self):
        if self.note_service is not None:
            return self.note_service

        from app.core.background_init import init_manager

        return init_manager.note_service

    @staticmethod
    def _note_to_evidence(note: Any) -> Evidence:
        return Evidence(
            id=str(getattr(note, "id", "")),
            source="note",
            title=str(getattr(note, "title", "无标题") or "无标题"),
            content=str(getattr(note, "content", "") or ""),
        )

    @staticmethod
    def _document_to_evidence(document: Document) -> Evidence:
        metadata = dict(document.metadata or {})
        content = document.page_content or ""
        evidence_id = _document_id(metadata, content)

        return Evidence(
            id=evidence_id,
            source="knowledge_base",
            title=_document_title(metadata),
            content=content,
            score=_metadata_score(metadata),
            metadata=metadata,
        )


def _document_id(metadata: dict[str, Any], content: str) -> str:
    for key in ("note_id", "id"):
        value = metadata.get(key)
        if value:
            return str(value)

    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]
    for key in ("source", "filename", "original_filename"):
        value = metadata.get(key)
        if value:
            return f"{value}-{digest}"

    return f"doc-{digest}"


def _document_title(metadata: dict[str, Any]) -> str:
    for key in ("title", "original_filename", "source", "filename"):
        value = metadata.get(key)
        if value:
            return str(value)
    return "知识库文档"


def _metadata_score(metadata: dict[str, Any]) -> float | None:
    score = metadata.get("score")
    if isinstance(score, int | float):
        return float(score)
    return None
