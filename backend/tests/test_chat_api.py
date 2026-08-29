"""聊天 / Agent / RAG / 会话 API 集成测试。"""
import asyncio
import json
from dataclasses import dataclass

from fastapi import HTTPException

from tests.fakes import TEST_USER_ID


async def _next_stream_chunk(response):
    return await response.body_iterator.__anext__()


@dataclass
class FakeAgenticRagResult:
    context: str


class FakeChatService:
    """ChatService 内存替身，用于隔离各 endpoint 的编排行为。"""

    def __init__(self):
        self.calls = []

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


async def test_rag_query_retired_returns_404(client, monkeypatch):
    """/chat/rag/query 已随 HyDE 旧链路退役（检索统一走 agentic RAG）。"""
    install_fake_chat_service(monkeypatch)
    resp = await client.post("/chat/rag/query", json={"query": "什么是RAG"}, headers={"Authorization": "Bearer x"})
    assert resp.status_code == 404


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
# Agent 流式（真实 Agentic RAG 管线 + 全部 mock 组件）
# ---------------------------------------------------------------------------
async def test_agent_query_stream_real_agentic_rag_pipeline(client, real_note_service, monkeypatch):
    # 检索层已切 Neo4j；测试环境（NEO4J_URI 被遮蔽）回落 MySQLGraphStore，
    # 无 Chunk 能力 → LocalRetriever 安全返回空证据，管线不触碰任何向量库。
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
    assert any(frame["type"] == "response" for frame in frames)
    assert frames[-1]["type"] == "done"
    response_frame = next(frame for frame in frames if frame["type"] == "response")
    assert response_frame["session_id"] == "sess-1"


async def test_agent_query_stream_rag_error_continues_with_empty_context(client, monkeypatch):
    """Agentic RAG 管线抛异常时，路由降级：不注入上下文、继续转发 Agent 流式响应。"""
    import app.router.chat as chat_module

    seen = {}

    class BrokenAgenticRagService:
        async def run(self, query, user_id, thinking_callback=None):
            raise RuntimeError("管线内部故障")

    async def fake_agent_stream(query, session_id, user_id, custom_tools=None, rag_context="", **kwargs):
        seen["rag_context"] = rag_context
        yield f'data: {json.dumps({"type": "response", "content": "降级回答", "session_id": session_id}, ensure_ascii=False)}\n\n'
        yield f'data: {json.dumps({"type": "done", "session_id": session_id}, ensure_ascii=False)}\n\n'

    monkeypatch.setattr(chat_module, "AgenticRagService", BrokenAgenticRagService)
    monkeypatch.setattr(chat_module, "get_agent_stream_response", fake_agent_stream)

    async with client.stream(
        "POST", "/chat/agent/query/stream",
        json={"query": "讲讲RAG", "session_id": "sess-err"},
        headers={"Authorization": "Bearer x"},
    ) as resp:
        assert resp.status_code == 200
        lines = [l async for l in resp.aiter_lines()]

    frames = [json.loads(l[6:]) for l in lines if l.startswith("data: ")]
    assert seen["rag_context"] == ""
    assert frames[-1]["type"] == "done"


# ---------------------------------------------------------------------------
# Agent 流式（RAG 前置管线路径）
# ---------------------------------------------------------------------------
async def test_agent_query_stream_with_rag(client, real_note_service, monkeypatch):
    import app.router.chat as chat_module

    class FakeAgenticRagService:
        async def run(self, query, user_id, thinking_callback=None):
            await thinking_callback({"type": "thinking", "stage": "local_retrieval", "content": "retrieved"})
            return FakeAgenticRagResult(context="[来源：知识库《RAG文档》]\n关于RAG的知识内容")

    async def fake_agent_stream(query, session_id, user_id, custom_tools=None, rag_context="", **kwargs):
        assert rag_context  # RAG 管线应已注入上下文
        yield f'data: {json.dumps({"type": "response", "content": "基于资料的回答", "session_id": session_id}, ensure_ascii=False)}\n\n'
        yield f'data: {json.dumps({"type": "done", "session_id": session_id}, ensure_ascii=False)}\n\n'

    monkeypatch.setattr(chat_module, "AgenticRagService", FakeAgenticRagService)
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


async def test_agent_query_stream_uses_agentic_rag_context_before_agent_response(client, monkeypatch):
    import app.router.chat as chat_module

    calls = []

    class FakeAgenticRagService:
        async def run(self, query, user_id, thinking_callback=None):
            calls.append((query, user_id))
            await thinking_callback({"type": "thinking", "stage": "agentic_plan", "content": "planned"})
            return FakeAgenticRagResult(context="[来源：外部搜索《Result》]\nFresh fact")

    async def fake_agent_stream(query, session_id, user_id, custom_tools=None, rag_context="", **kwargs):
        assert rag_context == "[来源：外部搜索《Result》]\nFresh fact"
        yield f'data: {json.dumps({"type": "response", "content": "基于证据的回答", "session_id": session_id}, ensure_ascii=False)}\n\n'
        yield f'data: {json.dumps({"type": "done", "session_id": session_id}, ensure_ascii=False)}\n\n'

    monkeypatch.setattr(chat_module, "AgenticRagService", FakeAgenticRagService)
    monkeypatch.setattr(chat_module, "get_agent_stream_response", fake_agent_stream)

    async with client.stream(
        "POST", "/chat/agent/query/stream",
        json={"query": "讲讲最新RAG", "session_id": "sess-agentic"},
        headers={"Authorization": "Bearer x"},
    ) as resp:
        assert resp.status_code == 200
        lines = [l async for l in resp.aiter_lines()]

    frames = [json.loads(l[6:]) for l in lines if l.startswith("data: ")]
    assert calls == [("讲讲最新RAG", TEST_USER_ID)]
    # 时序：路由占位 thinking（让前端折叠框立即出现）→ RAG 真实事件 → agent 响应 → done
    assert [frame["type"] for frame in frames] == ["thinking", "thinking", "response", "done"]
    assert frames[0]["stage"] == "agentic_plan"
    assert frames[0]["details"] == {"placeholder": True}
    assert frames[1]["stage"] == "agentic_plan"
    assert "details" not in frames[1]


async def test_agent_query_stream_requires_auth(raw_client):
    async with raw_client.stream(
        "POST", "/chat/agent/query/stream", json={"query": "hi"},
    ) as resp:
        assert resp.status_code == 401


async def test_agent_query_stream_emits_thinking_before_rag_finishes(monkeypatch):
    from app.router.chat import query_stream
    from app.schemas.models import QueryRequest

    rag_finished = asyncio.Event()

    class FakeAgenticRagService:
        async def run(self, query, user_id, thinking_callback=None):
            await thinking_callback({"type": "thinking", "stage": "agentic_plan", "content": "planned"})
            await rag_finished.wait()
            return FakeAgenticRagResult(context="complete context")

    async def fake_agent_stream(query, session_id, user_id, custom_tools=None, rag_context="", **kwargs):
        assert rag_context == "complete context"
        yield "agent response"

    import app.router.chat as chat_module

    monkeypatch.setattr(chat_module, "AgenticRagService", FakeAgenticRagService)
    monkeypatch.setattr(chat_module, "get_agent_stream_response", fake_agent_stream)

    response = await query_stream(QueryRequest(query="讲讲RAG"), user_id=TEST_USER_ID, _=None)
    stream_task = asyncio.create_task(_next_stream_chunk(response))
    # 第一帧是路由层占位 thinking（RAG 尚未产出任何事件）
    first_chunk = await asyncio.wait_for(stream_task, timeout=0.2)
    assert json.loads(first_chunk.removeprefix("data: ").strip()) == {
        "type": "thinking",
        "stage": "agentic_plan",
        "content": "正在规划检索策略…",
        "details": {"placeholder": True},
    }

    # RAG 管线的真实 thinking 事件仍先于 RAG 完成到达
    real_chunk = await asyncio.wait_for(_next_stream_chunk(response), timeout=0.2)
    assert json.loads(real_chunk.removeprefix("data: ").strip())["content"] == "planned"

    rag_finished.set()
    assert await asyncio.wait_for(_next_stream_chunk(response), timeout=0.2) == "agent response"
