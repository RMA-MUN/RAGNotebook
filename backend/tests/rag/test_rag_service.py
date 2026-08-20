"""rag_service.py — RagService 测试（全 fake：向量库 / 笔记库 / 重排序 / ChatModel）。"""
from langchain_core.documents import Document
from langchain_core.runnables import RunnableLambda

import app.rag.rag_service as rag_module
from app.rag.rag_service import RagService
from tests.conftest import install_fake_vector_store, install_init_manager_fakes
from tests.fakes import (
    TEST_USER_ID,
    FakeReorderService,
    FakeVectorStoreService,
    make_fake_chat_model,
)

NOT_FOUND_MSG = "抱歉，我没有找到相关的信息。"


class FakeNotesStore:
    """带 similarity_search 的笔记向量库替身。"""

    def __init__(self, documents):
        self.documents = documents

    def similarity_search(self, query, k=4, filter=None, **kwargs):
        return self.documents[:k]


class FakeNoteService:
    def __init__(self, documents):
        self.notes_store = FakeNotesStore(documents)


def _kb_doc(content, title=None):
    meta = {"original_filename": "a.pdf", "source": "a.pdf"}
    if title:
        meta["title"] = title
    return Document(page_content=content, metadata=meta)


def _note_doc(content, title="我的笔记"):
    return Document(page_content=content, metadata={"title": title})


def _build(
    monkeypatch,
    *,
    km_documents=None,
    note_documents=None,
    chat_model=None,
    reorder_service=None,
    user_id=TEST_USER_ID,
    thinking_callback=None,
):
    chat_model = chat_model or make_fake_chat_model()
    reorder_service = reorder_service or FakeReorderService()
    note_service = FakeNoteService(note_documents or [])

    # 按任务要求先安装 conftest 提供的替身
    install_fake_vector_store(monkeypatch, documents=km_documents or [])
    install_init_manager_fakes(
        monkeypatch,
        chat_model=chat_model,
        note_service=note_service,
        reorder_service=reorder_service,
    )
    # rag_service 模块内是 `from app.rag.vector_store import VectorStoreService`，
    # 需要直接替换 rag_service 命名空间中的绑定
    fake_vs = FakeVectorStoreService(documents=km_documents or [])
    monkeypatch.setattr(rag_module, "VectorStoreService", lambda *a, **kw: fake_vs)
    return RagService(user_id=user_id, thinking_callback=thinking_callback), fake_vs


class TestInit:
    def test_init_loads_prompt_and_chain(self, monkeypatch):
        service, _ = _build(monkeypatch)
        assert service.prompt_text  # rag_summary_prompt 已加载
        assert service.prompt_template is not None
        assert service.chain is not None
        assert service.note_service is not None
        assert service.vector_store is not None
        assert service.user_id == TEST_USER_ID


class TestGenerateHypotheticalDocument:
    async def test_returns_fake_chat_output(self, monkeypatch):
        service, _ = _build(monkeypatch, chat_model=make_fake_chat_model(["假设性文档"]))
        doc = await service.generate_hypothetical_document("问题")
        assert doc == "假设性文档"

    async def test_falls_back_to_query_on_exception(self, monkeypatch):
        service, _ = _build(monkeypatch)
        # 替换 chat_model 为抛异常的 Runnable → hyde 链失败 → 回退为原查询
        failing = RunnableLambda(lambda x: (_ for _ in ()).throw(RuntimeError("model down")))
        service.chat_model = failing
        assert await service.generate_hypothetical_document("原始查询") == "原始查询"


class TestRetrieveDocument:
    async def test_no_user_id_returns_empty(self, monkeypatch):
        service, _ = _build(monkeypatch, user_id=None)
        assert await service.retrieve_document("查询") == []

    async def test_merges_note_and_knowledge_docs_with_source_type(self, monkeypatch):
        kb_docs = [_kb_doc("知识库内容")]
        note_docs = [_note_doc("笔记内容")]
        service, _ = _build(monkeypatch, km_documents=kb_docs, note_documents=note_docs)

        result = await service.retrieve_document("问题")
        assert len(result) == 2
        # 顺序：笔记在前，知识库在后
        assert result[0].page_content == "笔记内容"
        assert result[0].metadata["source_type"] == "note"
        assert result[1].page_content == "知识库内容"
        assert result[1].metadata["source_type"] == "knowledge_base"

    async def test_initialize_retriever_sets_retriever(self, monkeypatch):
        service, _ = _build(monkeypatch, km_documents=[_kb_doc("x")])
        assert service.retriever is None
        await service.initialize_retriever("问题")
        assert service.retriever is not None


class TestReorderDocuments:
    async def test_returns_reordered_strings_on_success(self, monkeypatch):
        service, _ = _build(monkeypatch, km_documents=[_kb_doc("x")])
        docs = ["文档A", "文档B"]
        result = await service.reorder_documents("q", docs)
        # FakeReorderService 保持顺序并附相似度
        assert result == docs

    async def test_falls_back_to_original_on_failure(self, monkeypatch):
        service, _ = _build(monkeypatch, reorder_service=FakeReorderService(success=False))
        docs = ["文档A", "文档B"]
        result = await service.reorder_documents("q", docs)
        assert result == docs


class TestGetDocumentsAndSummary:
    async def test_single_doc_path(self, monkeypatch):
        service, _ = _build(
            monkeypatch,
            km_documents=[_kb_doc("唯一文档内容")],
            chat_model=make_fake_chat_model(["单篇摘要"]),
        )
        result = await service.get_documents_and_summary("问题")
        assert result["summary"] == "单篇摘要"
        assert len(result["documents"]) == 1
        assert "[来源：知识库《a.pdf》]" in result["documents"][0]
        assert "唯一文档内容" in result["documents"][0]

    async def test_multi_doc_path(self, monkeypatch):
        service, _ = _build(
            monkeypatch,
            km_documents=[_kb_doc(f"内容{i}") for i in range(1, 4)],
            chat_model=make_fake_chat_model(["hypo", "摘要1", "摘要2", "摘要3", "最终总结"]),
        )
        result = await service.get_documents_and_summary("问题")
        assert len(result["documents"]) == 3
        # hyde 用掉第 1 条，单文档摘要用掉 2~4 条，最终总结用第 5 条
        assert result["summary"] == "最终总结"

    async def test_note_source_formatting(self, monkeypatch):
        service, _ = _build(
            monkeypatch,
            note_documents=[_note_doc("笔记正文", title="读书笔记")],
            chat_model=make_fake_chat_model(["笔记摘要"]),
        )
        result = await service.get_documents_and_summary("问题")
        assert "[来源：笔记《读书笔记》]" in result["documents"][0]

    async def test_empty_documents_returns_not_found_message(self, monkeypatch):
        service, _ = _build(monkeypatch, km_documents=[], note_documents=[])
        result = await service.get_documents_and_summary("问题")
        assert result == {"documents": [], "summary": NOT_FOUND_MSG}

    async def test_no_user_id_returns_not_found_message(self, monkeypatch):
        service, _ = _build(monkeypatch, user_id=None)
        result = await service.get_documents_and_summary("问题")
        assert result == {"documents": [], "summary": NOT_FOUND_MSG}

    async def test_thinking_callback_records_stages(self, monkeypatch):
        calls = []

        async def cb(data):
            calls.append(data)

        service, _ = _build(
            monkeypatch,
            km_documents=[_kb_doc("内容")],
            chat_model=make_fake_chat_model(["s1"]),
            thinking_callback=cb,
        )
        await service.get_documents_and_summary("问题")
        stages = {c["stage"] for c in calls}
        assert "hyde" in stages
        assert "summarize" in stages


class TestRagSummary:
    async def test_returns_summary_string(self, monkeypatch):
        service, _ = _build(
            monkeypatch,
            km_documents=[_kb_doc("内容")],
            chat_model=make_fake_chat_model(["最终答案"]),
        )
        assert await service.rag_summary("问题") == "最终答案"

    async def test_empty_returns_not_found_message(self, monkeypatch):
        service, _ = _build(monkeypatch, km_documents=[])
        assert await service.rag_summary("问题") == NOT_FOUND_MSG