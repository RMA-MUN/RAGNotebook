"""LocalRetriever 检索层测试（Neo4j 种子检索架构）。

工具名不变、执行层走 GraphStore.search_chunks（向量+全文 RRF）与图扩展。
"""
import pytest

from app.graph.schemas.graph import ChunkHit
from app.graph.storage.neo4j_graph_store import Neo4jGraphStore
from app.rag.agentic_rag.evidence import merge_evidence
from app.rag.agentic_rag.local_retriever import LocalRetriever
from app.rag.agentic_rag.schemas import RetrievalStep


def _hit(chunk_id, kind, source_id, source_name, text, score=0.5):
    return ChunkHit(id=chunk_id, kind=kind, source_id=source_id, source_name=source_name,
                    chunk_index=0, text=text, score=score, metadata={"vector_score": score})


class FakeGraphEntity:
    def __init__(self, id, name, display_name, description=None, aliases=None, type_id=None):
        self.id = id
        self.name = name
        self.display_name = display_name
        self.description = description
        self.aliases = aliases or []
        self.type_id = type_id


class FakeGraphLink:
    def __init__(self, note_id, source_type="note", source_name=None):
        self.note_id = note_id
        self.source_type = source_type
        self.source_name = source_name


class FakeStore:
    def __init__(self, hits=None, entities=None, links_by_entity=None, mention_hits=None,
                 chunk_error=None):
        self.hits = hits or []
        self.entities = entities or []
        self.links_by_entity = links_by_entity or {}
        self.mention_hits = mention_hits or []
        self.chunk_error = chunk_error
        self.chunk_calls = []
        self.calls = []

    async def search_chunks(self, user_id, query_embedding, text_query, kinds, limit):
        self.chunk_calls.append({"user_id": user_id, "embedding": query_embedding,
                                 "query": text_query, "kinds": kinds, "limit": limit})
        if self.chunk_error:
            raise self.chunk_error
        filtered = [h for h in self.hits if not kinds or h.kind in kinds]
        return filtered[:limit]

    async def search_entities(self, user_id, query, limit):
        self.calls.append(("search_entities", user_id, query, limit))
        return self.entities[:limit]

    async def get_entity_notes(self, user_id, entity_id):
        self.calls.append(("get_entity_notes", user_id, entity_id))
        return self.links_by_entity.get(entity_id, [])

    async def get_chunks_mentioning(self, user_id, entity_ids, limit):
        self.calls.append(("get_chunks_mentioning", user_id, entity_ids, limit))
        return self.mention_hits[:limit]


class FakeSession:
    def __init__(self, store=None):
        self.store = store

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


class FakeExtractor:
    def __init__(self, names):
        self.names = names

    async def extract(self, query):
        return self.names


class FakeEmbedModel:
    def __init__(self, vector):
        self.vector = vector

    def embed_query(self, text):
        return self.vector


@pytest.fixture
def patch_store(monkeypatch):
    def _patch(store):
        import app.rag.agentic_rag.local_retriever as lm
        monkeypatch.setattr(lm, "get_graph_store", lambda session=None: store)
    return _patch


@pytest.fixture
def no_embed(monkeypatch):
    from app.core import background_init

    monkeypatch.setattr(background_init.init_manager, "embed_model", None)


@pytest.mark.asyncio
async def test_search_notes_returns_note_chunks(patch_store, no_embed):
    store = FakeStore(hits=[
        _hit("note:note-1:0", "note", "note-1", "Local Plan", "Use local notes first", 0.9),
        _hit("note:note-1:1", "note", "note-1", "Local Plan", "Second chunk", 0.8),
    ])
    patch_store(store)
    retriever = LocalRetriever(session_factory=FakeSession)

    evidences = await retriever.search(
        "user-1", [RetrievalStep(tool="search_notes", query="local rag", top_k=3)])

    assert store.chunk_calls[0]["kinds"] == ["note"]
    assert store.chunk_calls[0]["embedding"] is None
    assert len(evidences) == 2
    assert evidences[0].source == "note"
    assert evidences[0].id == "note:note-1:0"
    assert evidences[0].title == "Local Plan"
    assert evidences[0].content == "Use local notes first"
    assert evidences[0].score == 0.9
    assert evidences[0].metadata["source_id"] == "note-1"


@pytest.mark.asyncio
async def test_search_knowledge_base_returns_doc_chunks(patch_store, no_embed):
    store = FakeStore(hits=[
        _hit("doc:md5-1:0", "doc", "md5-1", "Guide.pdf", "Knowledge chunk", 0.82),
    ])
    patch_store(store)
    retriever = LocalRetriever(session_factory=FakeSession)

    evidences = await retriever.search(
        "user-1", [RetrievalStep(tool="search_knowledge_base", query="guide", top_k=5)])

    assert store.chunk_calls[0]["kinds"] == ["doc"]
    assert len(evidences) == 1
    assert evidences[0].source == "knowledge_base"
    assert evidences[0].title == "Guide.pdf"
    assert evidences[0].score == 0.82


@pytest.mark.asyncio
async def test_search_knowledge_base_limits_to_top_k(patch_store, no_embed):
    store = FakeStore(hits=[
        _hit(f"doc:md5:{i}", "doc", "md5", "f.pdf", f"chunk{i}") for i in range(3)])
    patch_store(store)
    retriever = LocalRetriever(session_factory=FakeSession)

    evidences = await retriever.search(
        "user-1", [RetrievalStep(tool="search_knowledge_base", query="guide", top_k=2)])

    assert [item.id for item in evidences] == ["doc:md5:0", "doc:md5:1"]


@pytest.mark.asyncio
async def test_hybrid_search_single_merged_call(patch_store, no_embed):
    store = FakeStore(hits=[
        _hit("note:note-1:0", "note", "note-1", "Note", "Note content"),
        _hit("doc:kb-1:0", "doc", "kb-1", "KB", "KB content"),
    ])
    patch_store(store)
    retriever = LocalRetriever(session_factory=FakeSession)

    evidences = await retriever.search(
        "user-1", [RetrievalStep(tool="hybrid_search", query="combined", top_k=5)])

    # 单次合并检索（kinds=None），不再分别查两套存储
    assert len(store.chunk_calls) == 1 and store.chunk_calls[0]["kinds"] is None
    assert [(item.source, item.id) for item in evidences] == [
        ("note", "note:note-1:0"), ("knowledge_base", "doc:kb-1:0")]
    merged = merge_evidence(evidences)
    assert [item.content for item in merged] == ["Note content", "KB content"]


@pytest.mark.asyncio
async def test_query_embedding_passed_to_store(patch_store, monkeypatch):
    from app.core import background_init

    monkeypatch.setattr(background_init.init_manager, "embed_model", FakeEmbedModel([0.1, 0.2]))
    store = FakeStore()
    patch_store(store)
    retriever = LocalRetriever(session_factory=FakeSession)

    await retriever.search("user-1", [RetrievalStep(tool="search_notes", query="q", top_k=3)])
    assert store.chunk_calls[0]["embedding"] == [0.1, 0.2]


@pytest.mark.asyncio
async def test_web_search_step_is_skipped_locally(patch_store, no_embed):
    store = FakeStore(hits=[_hit("doc:1:0", "doc", "1", "f", "x")])
    patch_store(store)
    retriever = LocalRetriever(session_factory=FakeSession)

    evidences = await retriever.search(
        "user-1", [RetrievalStep(tool="web_search", query="latest", top_k=5)])

    assert evidences == []
    assert store.chunk_calls == []


@pytest.mark.asyncio
async def test_store_failure_degrades_step_to_empty(patch_store, no_embed):
    """图存储检索失败（如 Neo4j 不可用）→ 该步降级为空证据，不阻塞其余步骤。"""
    store = FakeStore(chunk_error=RuntimeError("Neo4j 不可用"))
    patch_store(store)
    retriever = LocalRetriever(session_factory=FakeSession)

    evidences = await retriever.search(
        "user-1", [RetrievalStep(tool="hybrid_search", query="q", top_k=3)])
    assert evidences == []


@pytest.mark.asyncio
async def test_search_graph_converts_entity_with_description_and_notes(patch_store, no_embed):
    entity = FakeGraphEntity("ent-1", "DeepSeek", "DeepSeek",
                             description="开源大模型公司", aliases=["深度求索"], type_id="tech-id")
    link = FakeGraphLink("note-1", source_type="note", source_name="deepseek")
    store = FakeStore(entities=[entity], links_by_entity={"ent-1": [link]})
    patch_store(store)
    retriever = LocalRetriever(session_factory=FakeSession,
                               query_entity_extractor=FakeExtractor(["DeepSeek"]))

    evidences = await retriever.search(
        "user-1", [RetrievalStep(tool="search_graph", query="DeepSeek", top_k=3)])

    assert len(evidences) == 1
    assert evidences[0].id == "ent-1"
    assert evidences[0].source == "graph"
    assert evidences[0].title == "DeepSeek"
    assert "开源大模型公司" in evidences[0].content
    assert "deepseek" in evidences[0].content
    assert evidences[0].metadata["type_id"] == "tech-id"


@pytest.mark.asyncio
async def test_search_graph_uses_title_when_no_description_or_notes(patch_store, no_embed):
    entity = FakeGraphEntity("ent-2", "Shor", "Shor")
    store = FakeStore(entities=[entity])
    patch_store(store)
    retriever = LocalRetriever(session_factory=FakeSession,
                               query_entity_extractor=FakeExtractor(["Shor"]))

    evidences = await retriever.search(
        "user-1", [RetrievalStep(tool="search_graph", query="Shor", top_k=5)])
    assert evidences[0].content == "Shor"


class NeoFakeStore(FakeStore, Neo4jGraphStore):
    """以 Neo4jGraphStore 子类身份触发 isinstance 分支（方法全部走假件）。"""

    def __init__(self):
        FakeStore.__init__(
            self,
            entities=[FakeGraphEntity("ent-1", "DeepSeek", "DeepSeek")],
            mention_hits=[_hit("note:n1:0", "note", "n1", "笔记", "DeepSeek 是大模型公司")])
        self.session = None


@pytest.mark.asyncio
async def test_search_graph_appends_mentioned_chunks(patch_store, no_embed):
    """Neo4j 主路径：命中实体追加其 chunk 片段作为补充证据。"""
    store = NeoFakeStore()
    patch_store(store)
    retriever = LocalRetriever(session_factory=FakeSession,
                               query_entity_extractor=FakeExtractor(["DeepSeek"]))

    evidences = await retriever.search(
        "user-1", [RetrievalStep(tool="search_graph", query="DeepSeek", top_k=3)])

    assert [e.source for e in evidences] == ["graph", "note"]
    assert evidences[1].content == "DeepSeek 是大模型公司"
    assert store.calls[-1][0] == "get_chunks_mentioning"
