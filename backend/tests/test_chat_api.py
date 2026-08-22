"""聊天 / Agent / RAG / 会话 API 集成测试。"""
import json

from fastapi import HTTPException
from langchain_core.documents import Document

from tests.conftest import install_fake_vector_store
from tests.fakes import TEST_USER_ID


class FakeChatService:
    """ChatService 内存替身，用于隔离各 endpoint 的编排行为。"""

    def __init__(self):
        self.calls = []

    async def handle_agent_query(self, query, session_id, user_id):
        return "sid-1", "agent 回答", []

    async def handle_rag_query(self, query, user_id):
        return "RAG 摘要回答"

    async def handle_get_session(self, session_id, user_id):
        return [("你好", "你好呀")]

    async def handle_delete_session(self, session_id, user_id):
        self.calls.append(("delete", session_id, user_id))

    async def handle_get_all_sessions(self):
        return ["s1", "s2"]

    async def handle_get_user_sessions(self, user_id, current_user_id):
        if user_id != current_user_id:
            raise HTTPException(status_code=403, detail="Forbidden")
        return [{"id": user_id, "title": "t", "created_at": None, "updated_at": None}]

    async def handle_reorder(self, query, documents):
        return [{"document": d, "similarity": 0.9} for d in documents]


def install_fake_chat_service(monkeypatch):
    from main import app

    import app.router.chat as chat_module

    service = FakeChatService()
    app.dependency_overrides[chat_module.get_router_service] = lambda: service
    return service


async def test_rag_query(client, monkeypatch):
    service = install_fake_chat_service(monkeypatch)
    resp = await client.post("/chat/rag/query", json={"query": "什么是RAG"}, headers={"Authorization": "Bearer x"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == 200
    assert body["data"]["response"] == "RAG 摘要回答"
    assert service.calls == []


async def test_get_session(client, monkeypatch):
    install_fake_chat_service(monkeypatch)
    resp = await client.get("/chat/session/abc", headers={"Authorization": "Bearer x"})
    body = resp.json()
    assert body["data"]["session_id"] == "abc"
    assert body["data"]["history"] == [["你好", "你好呀"]]


async def test_delete_session(client, monkeypatch):
    service = install_fake_chat_service(monkeypatch)
    resp = await client.delete("/chat/session/abc", headers={"Authorization": "Bearer x"})
    assert resp.status_code == 200
    assert "deleted successfully" in resp.json()["message"]
    assert service.calls == [("delete", "abc", TEST_USER_ID)]


async def test_get_all_sessions(client, monkeypatch):
    install_fake_chat_service(monkeypatch)
    resp = await client.get("/chat/sessions", headers={"Authorization": "Bearer x"})
    assert resp.json()["data"]["sessions"] == ["s1", "s2"]


async def test_get_user_sessions_ok(client, monkeypatch):
    install_fake_chat_service(monkeypatch)
    resp = await client.get(f"/chat/sessions/{TEST_USER_ID}", headers={"Authorization": "Bearer x"})
    assert resp.status_code == 200
    assert resp.json()["data"]["sessions"] == [{"id": TEST_USER_ID, "title": "t", "created_at": None, "updated_at": None}]


async def test_get_user_sessions_forbidden(client, monkeypatch):
    install_fake_chat_service(monkeypatch)
    resp = await client.get("/chat/sessions/other-user", headers={"Authorization": "Bearer x"})
    assert resp.status_code == 403
    assert resp.json()["code"] == 403


async def test_reorder_documents(client, monkeypatch):
    install_fake_chat_service(monkeypatch)
    resp = await client.post("/chat/reorder", json={"query": "q", "documents": ["a", "b"]},
                             headers={"Authorization": "Bearer x"})
    assert resp.status_code == 200
    docs = resp.json()["data"]["documents"]
    assert docs == [{"document": "a", "similarity": 0.9}, {"document": "b", "similarity": 0.9}]


# ---------------------------------------------------------------------------
# Agent 流式（skip RAG 路径）
# ---------------------------------------------------------------------------
async def test_agent_query_stream_skip_rag(client, real_note_service, monkeypatch):
    install_fake_vector_store(monkeypatch, route_score=0.0)

    import app.router.chat as chat_module

    async def fake_agent_stream(query, session_id, user_id, custom_tools=None, rag_context="", **kwargs):
        yield f'data: {json.dumps({"type": "response", "content": "你好", "session_id": session_id}, ensure_ascii=False)}\n\n'
        yield f'data: {json.dumps({"type": "done", "session_id": session_id}, ensure_ascii=False)}\n\n'

    monkeypatch.setattr(chat_module, "get_agent_stream_response", fake_agent_stream)

    async with client.stream(
        "POST", "/chat/agent/query/stream",
        json={"query": "你好", "session_id": "sess-1"},
        headers={"Authorization": "Bearer x"},
    ) as resp:
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
        lines = [l async for l in resp.aiter_lines()]

    frames = [json.loads(l[6:]) for l in lines if l.startswith("data: ")]
    assert frames[0]["type"] == "response"
    assert frames[-1]["type"] == "done"
    assert frames[0]["session_id"] == "sess-1"


# ---------------------------------------------------------------------------
# Agent 流式（RAG 前置管线路径）
# ---------------------------------------------------------------------------
async def test_agent_query_stream_with_rag(client, real_note_service, monkeypatch):
    docs = [
        Document(page_content="关于RAG的知识内容", metadata={
            "source_type": "knowledge_base", "original_filename": "rag_doc.txt",
            "user_id": TEST_USER_ID, "title": "RAG文档"}),
    ]
    fake_vs = install_fake_vector_store(monkeypatch, route_score=1.0, documents=docs)

    # rag_service 顶层 `from ... import VectorStoreService` 绑定了真实类，需单独替换
    import app.rag.rag_service as rag_service_module

    monkeypatch.setattr(rag_service_module, "VectorStoreService", lambda *a, **k: fake_vs)

    import app.router.chat as chat_module

    async def fake_agent_stream(query, session_id, user_id, custom_tools=None, rag_context="", **kwargs):
        assert rag_context  # RAG 管线应已注入上下文
        yield f'data: {json.dumps({"type": "response", "content": "基于资料的回答", "session_id": session_id}, ensure_ascii=False)}\n\n'
        yield f'data: {json.dumps({"type": "done", "session_id": session_id}, ensure_ascii=False)}\n\n'

    monkeypatch.setattr(chat_module, "get_agent_stream_response", fake_agent_stream)

    async with client.stream(
        "POST", "/chat/agent/query/stream",
        json={"query": "讲讲RAG"},
        headers={"Authorization": "Bearer x"},
    ) as resp:
        assert resp.status_code == 200
        lines = [l async for l in resp.aiter_lines()]

    frames = [json.loads(l[6:]) for l in lines if l.startswith("data: ")]
    # 存在 RAG 思考事件 + agent 响应
    assert any(f.get("type") == "thinking" for f in frames)
    assert frames[-1]["type"] == "done"


async def test_agent_query_stream_requires_auth(raw_client):
    async with raw_client.stream(
        "POST", "/chat/agent/query/stream", json={"query": "hi"},
    ) as resp:
        assert resp.status_code == 401