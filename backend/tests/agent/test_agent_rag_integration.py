"""端到端集成测试：真实 create_agent 编译图触发 search_rag 工具。

复用 test_agent.py Level C 手法（注入能发出 search_rag tool_call 的假聊天模型，
不 mock create_agent），验证：
- search_rag 被真实绑定并被 LangGraph 真实执行；
- 工具输出进入最终回答；
- 护栏返回「已覆盖」提示后不再发起第二次真实检索。
"""
import pytest
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage

from app.agent import agent as agent_module
from app.agent.agent import get_agent_response
from app.agent.agent_rag_tool import init_rag_guard, reset_rag_guard
from app.agent.agent_tools import set_current_user_id
from app.rag.agentic_rag.service import AgenticRagService


class ToolCallingRagFakeModel(FakeMessagesListChatModel):
    """按序吐响应的假模型：首次返回 search_rag tool_call，之后返回最终答复。"""

    def bind_tools(self, tools, **kwargs):
        return self


@pytest.fixture(autouse=True)
def _isolate_request_context():
    """隔离请求级 ContextVar（current_user_id + rag_guard），防用例间串扰。"""
    reset_rag_guard()
    set_current_user_id("u1")
    yield
    reset_rag_guard()


@pytest.mark.asyncio
async def test_real_agent_calls_search_rag_once(monkeypatch):
    """真实 create_agent 中，Agent 收到 tool_call 后执行 search_rag，工具结果入回答。"""
    calls = []

    def _fake_init(self, *args, **kwargs):
        pass

    async def _fake_run(self, query, user_id, thinking_callback=None):
        calls.append(query)
        return type("R", (), {
            "context": "来自图谱的证据片段",
            "evidences": [type("E", (), {"source": "graph"})()],
            "used_web": False,
        })()

    monkeypatch.setattr(AgenticRagService, "__init__", _fake_init)
    monkeypatch.setattr(AgenticRagService, "run", _fake_run)

    model = ToolCallingRagFakeModel(responses=[
        AIMessage(content="", tool_calls=[
            {"name": "search_rag", "args": {"query": "图谱补充维度"}, "id": "call_1"},
        ]),
        AIMessage(content="基于补充证据回答完成。"),
    ])
    monkeypatch.setattr(agent_module.agent_factory, "_create_chat_model", lambda custom_model=None: model)

    result = await get_agent_response("原始问题", user_id="u1")
    assert result["response"] == "基于补充证据回答完成。"
    assert calls == ["图谱补充维度"]
    assert any(step["tool"] == "search_rag" for step in result["steps"])


@pytest.mark.asyncio
async def test_real_agent_stops_after_covered_hint(monkeypatch):
    """护栏返回「已覆盖」提示后，Agent 不再发起第二次 search_rag。"""
    calls = []

    def _fake_init(self, *args, **kwargs):
        pass

    async def _fake_run(self, query, user_id, thinking_callback=None):
        calls.append(query)
        raise AssertionError("不应真正执行 RAG：该 query 已被护栏短路")

    monkeypatch.setattr(AgenticRagService, "__init__", _fake_init)
    monkeypatch.setattr(AgenticRagService, "run", _fake_run)

    init_rag_guard(["原问题"])
    try:
        model = ToolCallingRagFakeModel(responses=[
            AIMessage(content="", tool_calls=[
                {"name": "search_rag", "args": {"query": "原问题"}, "id": "call_1"},
            ]),
            AIMessage(content="好的，停止检索。"),
        ])
        monkeypatch.setattr(agent_module.agent_factory, "_create_chat_model", lambda custom_model=None: model)
        result = await get_agent_response("原问题", user_id="u1")
        assert calls == []
        assert any(step["tool"] == "search_rag" for step in result["steps"])
    finally:
        reset_rag_guard()
