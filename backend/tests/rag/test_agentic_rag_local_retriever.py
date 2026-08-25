from dataclasses import dataclass

import pytest
from langchain_core.documents import Document

from app.rag.agentic_rag.evidence import merge_evidence
from app.rag.agentic_rag.local_retriever import LocalRetriever
from app.rag.agentic_rag.schemas import RetrievalStep


@dataclass
class FakeNote:
    id: str
    title: str
    content: str


class FakeNoteService:
    def __init__(self, notes):
        self.notes = notes
        self.calls = []

    async def search_notes(self, db, user_id: str, query: str, top_k: int = 10):
        self.calls.append({"db": db, "user_id": user_id, "query": query, "top_k": top_k})
        return self.notes[:top_k]


class FakeSession:
    async def __aenter__(self):
        return "db-session"

    async def __aexit__(self, exc_type, exc, tb):
        return False


class FakeRetriever:
    def __init__(self, documents):
        self.documents = documents
        self.queries = []

    async def ainvoke(self, query: str):
        self.queries.append(query)
        return self.documents


class FakeVectorStore:
    def __init__(self, documents):
        self.retriever = FakeRetriever(documents)
        self.calls = []

    async def get_retriever(self, query: str, user_id: str):
        self.calls.append({"query": query, "user_id": user_id})
        return self.retriever


@pytest.mark.asyncio
async def test_search_notes_step_converts_note_results_to_evidence():
    note_service = FakeNoteService([
        FakeNote(id="note-1", title="Local Plan", content="Use local notes first"),
    ])
    retriever = LocalRetriever(
        note_service=note_service,
        vector_store=FakeVectorStore([]),
        session_factory=FakeSession,
    )

    evidences = await retriever.search(
        "user-1",
        [RetrievalStep(tool="search_notes", query="local rag", top_k=3)],
    )

    assert len(evidences) == 1
    assert evidences[0].id == "note-1"
    assert evidences[0].source == "note"
    assert evidences[0].title == "Local Plan"
    assert evidences[0].content == "Use local notes first"
    assert note_service.calls == [
        {"db": "db-session", "user_id": "user-1", "query": "local rag", "top_k": 3}
    ]


@pytest.mark.asyncio
async def test_search_knowledge_base_step_converts_documents_to_evidence():
    vector_store = FakeVectorStore([
        Document(
            page_content="Knowledge chunk",
            metadata={"note_id": "doc-1", "original_filename": "Guide.pdf", "score": 0.82},
        ),
    ])
    retriever = LocalRetriever(
        note_service=FakeNoteService([]),
        vector_store=vector_store,
        session_factory=FakeSession,
    )

    evidences = await retriever.search(
        "user-1",
        [RetrievalStep(tool="search_knowledge_base", query="guide", top_k=5)],
    )

    assert len(evidences) == 1
    assert evidences[0].id == "doc-1"
    assert evidences[0].source == "knowledge_base"
    assert evidences[0].title == "Guide.pdf"
    assert evidences[0].content == "Knowledge chunk"
    assert evidences[0].score == 0.82
    assert vector_store.calls == [{"query": "guide", "user_id": "user-1"}]
    assert vector_store.retriever.queries == ["guide"]


@pytest.mark.asyncio
async def test_search_knowledge_base_step_limits_document_results_to_top_k():
    vector_store = FakeVectorStore([
        Document(page_content="First", metadata={"id": "kb-1"}),
        Document(page_content="Second", metadata={"id": "kb-2"}),
        Document(page_content="Third", metadata={"id": "kb-3"}),
    ])
    retriever = LocalRetriever(
        note_service=FakeNoteService([]),
        vector_store=vector_store,
        session_factory=FakeSession,
    )

    evidences = await retriever.search(
        "user-1",
        [RetrievalStep(tool="search_knowledge_base", query="guide", top_k=2)],
    )

    assert [item.id for item in evidences] == ["kb-1", "kb-2"]


@pytest.mark.asyncio
async def test_search_knowledge_base_keeps_distinct_chunks_from_same_file():
    vector_store = FakeVectorStore([
        Document(
            page_content="Revenue grew from enterprise expansion.",
            metadata={"original_filename": "report.pdf", "page": 1},
        ),
        Document(
            page_content="Gross margin improved after vendor renegotiation.",
            metadata={"original_filename": "report.pdf", "page": 2},
        ),
    ])
    retriever = LocalRetriever(
        note_service=FakeNoteService([]),
        vector_store=vector_store,
        session_factory=FakeSession,
    )

    evidences = await retriever.search(
        "user-1",
        [RetrievalStep(tool="search_knowledge_base", query="report", top_k=5)],
    )
    merged = merge_evidence(evidences)

    assert len(evidences) == 2
    assert evidences[0].id != evidences[1].id
    assert [item.content for item in merged] == [
        "Revenue grew from enterprise expansion.",
        "Gross margin improved after vendor renegotiation.",
    ]


@pytest.mark.asyncio
async def test_hybrid_search_combines_notes_and_knowledge_base():
    note_service = FakeNoteService([
        FakeNote(id="note-1", title="Note", content="Note content"),
    ])
    vector_store = FakeVectorStore([
        Document(page_content="KB content", metadata={"id": "kb-1", "title": "KB"}),
    ])
    retriever = LocalRetriever(
        note_service=note_service,
        vector_store=vector_store,
        session_factory=FakeSession,
    )

    evidences = await retriever.search(
        "user-1",
        [RetrievalStep(tool="hybrid_search", query="combined", top_k=2)],
    )

    assert [(item.source, item.id, item.title) for item in evidences] == [
        ("note", "note-1", "Note"),
        ("knowledge_base", "kb-1", "KB"),
    ]


@pytest.mark.asyncio
async def test_web_search_step_is_skipped_locally():
    note_service = FakeNoteService([FakeNote(id="note-1", title="Note", content="Note content")])
    vector_store = FakeVectorStore([Document(page_content="KB content", metadata={"id": "kb-1"})])
    retriever = LocalRetriever(
        note_service=note_service,
        vector_store=vector_store,
        session_factory=FakeSession,
    )

    evidences = await retriever.search(
        "user-1",
        [RetrievalStep(tool="web_search", query="latest", top_k=5)],
    )

    assert evidences == []
    assert note_service.calls == []
    assert vector_store.calls == []
