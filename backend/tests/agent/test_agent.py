"""编排层测试：AgentFactory / get_agent_response / get_agent_stream_response。

Level A（推荐）：monkeypatch `agent_factory.create_agent` 返回
`FakeAgent`，测试编排逻辑（历史构建、消息拼接、异常回退、SSE 帧序列、
规划阶段 token 过滤、会话写入）。
Level C（尽力而为的真端到端）：不 mock create_agent，给工厂注入一个
能发出真实 tool_call AIMessage 的假模型，让 create_agent 真正调用一次工具。
"""
import json

import pytest_asyncio
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage, ToolMessage
from langchain_core.tools import tool
from langgraph.graph.state import CompiledStateGraph
from sqlalchemy import select

import app.services as services_module
from app.agent import agent as agent_module
from app.agent.agent import (
    AgentFactory,
    get_agent,
    get_agent_response,
    get_agent_stream_response,
)
from app.models.chat_history import ChatMessage, ChatSession
from tests.conftest import patch_session_factory
from tests.fakes import FakeAgent


# ---------------------------------------------------------------------------
# 本地 fixtures / 事件构造器
# ---------------------------------------------------------------------------
@pytest_asyncio.fixture
async def patched_db(monkeypatch, session_factory):
    """把所有 AsyncSessionLocal 引用替换为内存 SQLite 会话工厂。"""
    patch_session_factory(monkeypatch, session_factory)
    yield session_factory


@pytest_asyncio.fixture
def fresh_session_manager(monkeypatch):
    """清掉 session_manager 代理缓存的 DatabaseSessionManager 单例，
    保证每个测试在 patch 后的 AsyncSessionLocal 上惰性创建全新实例。"""
    monkeypatch.setattr(services_module, "database_session_manager", None)


def _model_run_events(run_id: str, texts: list[str]) -> list[dict]:
    """最终回答轮事件流：逐块 on_chat_model_stream（纯文本 chunk，实时转发）。"""
    return [
        {
            "event": "on_chat_model_stream",
            "name": "FakeChatModel",
            "run_id": run_id,
            "data": {"chunk": AIMessage(content=text)},
        }
        for text in texts
    ]


def _planning_stream_events(run_id: str) -> list[dict]:
    """规划轮事件流：chunk 携带 tool_call_chunks（工具参数增量）→ 应被过滤。"""
    return [
        {
            "event": "on_chat_model_stream",
            "name": "FakeChatModel",
            "run_id": run_id,
            "data": {
                "chunk": AIMessageChunk(
                    content="",
                    tool_call_chunks=[{
                        "name": "search_notes_tool",
                        "args": '{"query": "测试"}',
                        "id": "call_1",
                        "index": 0,
                        "type": "tool_call_chunk",
                    }],
                ),
            },
        },
    ]


def _tool_run_events(name: str, tool_input: dict, tool_output: str) -> list[dict]:
    """工具调用事件流：on_tool_start + on_tool_end。"""
    return [
        {"event": "on_tool_start", "name": name, "data": {"input": tool_input}},
        {"event": "on_tool_end", "name": name, "data": {"output": tool_output}},
    ]


def _agent_run_events(*, planning: int = 0,
                      tools: list[tuple[str, dict, str]] | None = None,
                      final: list[str] | None = None) -> list[dict]:
    """一轮 agent 执行的事件流：可选规划轮（tool_call_chunks）/工具/最终回答（纯文本）。"""
    events = []
    for _ in range(planning):
        events += _planning_stream_events("run-planning")
    for name, tool_input, tool_output in tools or []:
        events += _tool_run_events(name, tool_input, tool_output)
    if final:
        events += _model_run_events("run-final", final)
    return events


class RecordingAgent(FakeAgent):
    """记录 astream_events/ainvoke 收到的输入，并可注入额外事件。"""

    def __init__(self, events=None, messages=None):
        super().__init__(messages=messages, events=events)


class BrokenAgent:
    """流式执行直接抛异常的替身 agent（async 生成器，异常在迭代时抛出）。"""

    async def astream_events(self, inputs, version="v2"):
        if False:  # pragma: no cover
            yield None
        raise RuntimeError("代理内部异常")


async def _collect_stream(*args, **kwargs):
    """直接消费异步生成器，绕开 HTTP。"""
    return [frame async for frame in get_agent_stream_response(*args, **kwargs)]


def _parse_frames(raw_frames):
    events = []
    for raw in raw_frames:
        body = raw[len("data: "):].rstrip("\n")
        events.append(json.loads(body))
    return events


# ---------------------------------------------------------------------------
# AgentFactory
# ---------------------------------------------------------------------------
def test_factory_returns_8_default_tools():
    names = {t.name for t in AgentFactory._get_default_tools()}
    assert names == {
        "what_time_is_now",
        "get_user_info_tools",
        "search_notes_tool",
        "get_note_stats_tool",
        "get_today_reviews_tool",
        "mark_reviewed_tool",
        "create_note_tool",
        "get_related_notes_tool",
    }


def test_get_agent_returns_compiled_state_graph(monkeypatch):
    """真实工厂路径：给定 KEY 后能构造出 create_agent 的编译图（不真正调用 LLM）。"""
    monkeypatch.setenv("OPENAI_API_KEY", "dummy-key-for-construction-test")
    agent = get_agent()
    assert isinstance(agent, CompiledStateGraph)


async def test_agent_factory_injects_custom_tools(monkeypatch):
    """custom_tools 会绕过默认工具列表（用假 agent 观察 tools 参数）。"""
    seen = {}

    def fake_factory(custom_tools=None, **kwargs):
        seen["tools"] = custom_tools
        return FakeAgent()

    @tool
    def dummy_tool(x: str) -> str:
        """占位工具（不真正使用）。"""
        return x

    monkeypatch.setattr(agent_module.agent_factory, "create_agent", fake_factory)
    my_tools = [dummy_tool]
    await get_agent_response("你好", custom_tools=my_tools, user_id="u1")
    assert seen["tools"] is my_tools


# ---------------------------------------------------------------------------
# get_agent_response（Level A）
# ---------------------------------------------------------------------------
async def test_get_agent_response_returns_final_message(monkeypatch):
    fake = FakeAgent(messages=[AIMessage(content="答复")])
    monkeypatch.setattr(agent_module.agent_factory, "create_agent", lambda **kw: fake)

    result = await get_agent_response("你好", user_id="u1")
    assert result["response"] == "答复"
    assert result["steps"] == []


async def test_get_agent_response_fallback_when_no_content(monkeypatch):
    fake = FakeAgent(messages=[AIMessage(content="")])
    monkeypatch.setattr(agent_module.agent_factory, "create_agent", lambda **kw: fake)

    result = await get_agent_response("你好", user_id="u1")
    assert result["response"] == "抱歉，我无法理解您的请求。"


async def test_get_agent_response_history_building(monkeypatch):
    fake = RecordingAgent(messages=[AIMessage(content="答复")])
    monkeypatch.setattr(agent_module.agent_factory, "create_agent", lambda **kw: fake)

    history = [("用户问题", "助手回答")]
    result = await get_agent_response("新问题", history=history, user_id="u1")

    assert result["response"] == "答复"
    messages = fake.inputs[0]["messages"]
    assert [type(m).__name__ for m in messages] == ["HumanMessage", "AIMessage", "HumanMessage"]
    assert [m.content for m in messages] == ["用户问题", "助手回答", "新问题"]


async def test_get_agent_response_collects_steps(monkeypatch):
    fake = FakeAgent(messages=[
        AIMessage(content="", tool_calls=[
            {"name": "search_notes_tool", "args": {"query": "测试"}, "id": "call_1"},
        ]),
        ToolMessage(content="搜索结果内容", tool_call_id="call_1"),
        AIMessage(content="最终回答"),
    ])
    monkeypatch.setattr(agent_module.agent_factory, "create_agent", lambda **kw: fake)

    result = await get_agent_response("帮我找笔记", user_id="u1")
    assert result["response"] == "最终回答"
    assert len(result["steps"]) == 1
    step = result["steps"][0]
    assert step["tool"] == "search_notes_tool"
    assert step["tool_input"] == {"query": "测试"}
    assert step["tool_output"] == "搜索结果内容"


async def test_get_agent_response_exception_fallback(monkeypatch):
    def boom(**kwargs):
        raise RuntimeError("模型连接失败")

    monkeypatch.setattr(agent_module.agent_factory, "create_agent", boom)

    result = await get_agent_response("你好", user_id="u1")
    assert result["response"] == "抱歉，处理您的请求时出现了错误: 模型连接失败"
    assert result["steps"] == []


# ---------------------------------------------------------------------------
# get_agent_stream_response（Level A）
# ---------------------------------------------------------------------------
async def test_get_agent_stream_response_full_flow(monkeypatch, patched_db, fresh_session_manager):
    response_text = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    fake = FakeAgent(events=_agent_run_events(final=[response_text]))
    monkeypatch.setattr(agent_module.agent_factory, "create_agent", lambda **kw: fake)

    frames = await _collect_stream("你好", session_id="s1", user_id="u1")
    events = _parse_frames(frames)

    # 1) 首帧：空内容 response + session_id
    assert events[0]["type"] == "response"
    assert events[0]["content"] == ""
    assert events[0]["session_id"] == "s1"

    # 2) 无 error 帧
    assert all(e["type"] != "error" for e in events)

    # 3) response 内容由最终模型调用的流事件原样转发
    assert "".join(e["content"] for e in events if e["type"] == "response" and e["content"]) == response_text
    assert [e["content"] for e in events if e["type"] == "response" and e["content"]] == [
        response_text,
    ]

    # 4) 结束帧：done + session_id
    assert events[-1]["type"] == "done"
    assert events[-1]["session_id"] == "s1"

    # 5) add_message 已把 user/assistant 对写入 SQLite
    async with patched_db() as db:
        sess = (await db.execute(select(ChatSession).where(ChatSession.id == "s1"))).scalar_one()
        assert sess.user_id == "u1"
        msgs = (await db.execute(
            select(ChatMessage).where(ChatMessage.session_id == "s1").order_by(ChatMessage.id)
        )).scalars().all()
    assert [m.role for m in msgs] == ["user", "assistant"]
    assert [m.content for m in msgs] == ["你好", response_text]


async def test_get_agent_stream_response_emits_tool_start_and_end_events(
    monkeypatch, patched_db, fresh_session_manager
):
    fake = FakeAgent(events=_agent_run_events(tools=[
        ("search_notes_tool", {"query": "测试"}, "真实搜索结果"),
    ]))
    monkeypatch.setattr(agent_module.agent_factory, "create_agent", lambda **kw: fake)

    frames = await _collect_stream("帮我找笔记", session_id="s1", user_id="u1")
    events = _parse_frames(frames)
    thinking = [event for event in events if event["type"] == "thinking"]

    assert thinking == [
        {
            "type": "thinking",
            "stage": "tool_start",
            "content": "正在调用 search_notes_tool",
            "details": {"tool": "search_notes_tool", "tool_input": {"query": "测试"}},
        },
        {
            "type": "thinking",
            "stage": "tool_end",
            "content": "search_notes_tool 执行完成",
            "details": {"tool": "search_notes_tool", "tool_output": "真实搜索结果"},
        },
    ]


async def test_get_agent_stream_response_forwards_final_tokens_immediately(
    monkeypatch, patched_db, fresh_session_manager
):
    """最终回答的纯文本 chunk 逐块实时转发（打字机效果）。"""
    fake = FakeAgent(events=_agent_run_events(final=["第", "一"]))
    monkeypatch.setattr(agent_module.agent_factory, "create_agent", lambda **kw: fake)

    frames = await _collect_stream("你好", session_id="s1", user_id="u1")
    events = _parse_frames(frames)
    response_events = [
        event for event in events if event["type"] == "response" and event["content"]
    ]

    assert [event["content"] for event in response_events] == ["第", "一"]


async def test_get_agent_stream_response_tool_end_with_toolmessage_output(
    monkeypatch, patched_db, fresh_session_manager
):
    """LangGraph 的 on_tool_end output 是 ToolMessage，必须归一化为 content 字符串。"""
    fake = FakeAgent(events=[
        {
            "event": "on_tool_start",
            "name": "search_notes_tool",
            "data": {"input": {"query": "deepseek"}},
        },
        {
            "event": "on_tool_end",
            "name": "search_notes_tool",
            "data": {"output": ToolMessage(content="搜索结果", tool_call_id="call_1")},
        },
    ])
    monkeypatch.setattr(agent_module.agent_factory, "create_agent", lambda **kw: fake)

    frames = await _collect_stream("给我介绍一下deepseek", session_id="s1", user_id="u1")
    events = _parse_frames(frames)

    assert all(e["type"] != "error" for e in events)
    thinking = [e for e in events if e["type"] == "thinking"]
    tool_end = next(e for e in thinking if e["stage"] == "tool_end")
    assert tool_end["details"]["tool_output"] == "搜索结果"
    assert events[-1]["type"] == "done"


async def test_get_agent_stream_response_filters_planning_run_tokens(
    monkeypatch, patched_db, fresh_session_manager
):
    """规划轮（tool_call_chunks 增量）token 不得流入 response，最终回答实时转发。"""
    fake = FakeAgent(events=_agent_run_events(
        planning=1,
        tools=[("search_notes_tool", {"query": "测试"}, "搜索结果")],
        final=["最终回答"],
    ))
    monkeypatch.setattr(agent_module.agent_factory, "create_agent", lambda **kw: fake)

    frames = await _collect_stream("帮我找笔记", session_id="s1", user_id="u1")
    events_parsed = _parse_frames(frames)
    response_events = [
        event for event in events_parsed if event["type"] == "response" and event["content"]
    ]

    assert [event["content"] for event in response_events] == ["最终回答"]


async def test_get_agent_stream_response_agent_error(monkeypatch, patched_db, fresh_session_manager):
    monkeypatch.setattr(agent_module.agent_factory, "create_agent", lambda **kw: BrokenAgent())

    frames = await _collect_stream("你好", session_id="s1", user_id="u1")
    events = _parse_frames(frames)

    assert events[0]["type"] == "response"
    errors = [e for e in events if e["type"] == "error"]
    assert len(errors) == 1
    assert "代理内部异常" in errors[0]["content"]
    assert events[-1]["type"] == "done"

    # 出错路径不写会话历史
    async with patched_db() as db:
        msgs = (await db.execute(
            select(ChatMessage).where(ChatMessage.session_id == "s1")
        )).scalars().all()
    assert msgs == []


async def test_get_agent_stream_response_uses_db_history(monkeypatch, patched_db, fresh_session_manager):
    async with patched_db() as db:
        db.add(ChatSession(id="s1", user_id="u1", title="历史会话"))
        await db.commit()
        db.add_all([
            ChatMessage(session_id="s1", role="user", content="旧问题"),
            ChatMessage(session_id="s1", role="assistant", content="旧回答"),
        ])
        await db.commit()

    fake = RecordingAgent(events=_agent_run_events(final=["新回答"]))
    monkeypatch.setattr(agent_module.agent_factory, "create_agent", lambda **kw: fake)

    frames = await _collect_stream("新问题", session_id="s1", user_id="u1")
    events = _parse_frames(frames)
    assert events[-1]["type"] == "done"

    # run_agent 先从 DB 加载历史并构造消息序列
    messages = fake.inputs[0]["messages"]
    assert [type(m).__name__ for m in messages] == ["HumanMessage", "AIMessage", "HumanMessage"]
    assert [m.content for m in messages] == ["旧问题", "旧回答", "新问题"]

    # add_message 又在末尾追加了新的 user/assistant 对
    async with patched_db() as db:
        msgs = (await db.execute(
            select(ChatMessage).where(ChatMessage.session_id == "s1").order_by(ChatMessage.id)
        )).scalars().all()
    assert [m.content for m in msgs] == ["旧问题", "旧回答", "新问题", "新回答"]


async def test_get_agent_stream_response_with_rag_context(monkeypatch, patched_db, fresh_session_manager):
    seen = {}

    def capturing_factory(custom_system_prompt=None, **kwargs):
        seen["system_prompt"] = custom_system_prompt
        return FakeAgent(events=_agent_run_events(final=["基于资料的回答"]))

    monkeypatch.setattr(agent_module.agent_factory, "create_agent", capturing_factory)

    rag_context = "[来源：知识库《Local》]\n本地资料\n\n[来源：外部搜索《Web》]\n外部资料"
    frames = await _collect_stream("问题", session_id="s1", user_id="u1", rag_context=rag_context)
    events = _parse_frames(frames)
    assert events[-1]["type"] == "done"

    system_prompt = seen["system_prompt"]
    assert system_prompt is not None
    assert rag_context in system_prompt
    assert "参考资料" in system_prompt
    assert "区分本地证据" in system_prompt
    assert "外部搜索证据" in system_prompt
    assert "证据不足" in system_prompt


async def test_get_agent_stream_response_default_system_prompt_without_rag(
    monkeypatch, patched_db, fresh_session_manager
):
    seen = {}

    def capturing_factory(custom_system_prompt=None, **kwargs):
        seen["system_prompt"] = custom_system_prompt
        return FakeAgent(events=_agent_run_events(final=["回答"]))

    monkeypatch.setattr(agent_module.agent_factory, "create_agent", capturing_factory)

    frames = await _collect_stream("你好", session_id="s1", user_id="u1")
    events = _parse_frames(frames)
    assert events[-1]["type"] == "done"

    assert seen["system_prompt"] == agent_module.agent_factory.default_system_prompt


# ---------------------------------------------------------------------------
# Level C（尽力而为的真端到端）
# ---------------------------------------------------------------------------
async def test_level_c_end_to_end_real_tool_calling(monkeypatch):
    """不 mock create_agent：真实 create_agent 编译图，
    仅注入一个能发出 tool_call AIMessage 的假聊天模型，验证真实工具被调用。"""
    from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel

    class ToolCallingFakeModel(FakeMessagesListChatModel):
        """第一次调用返回 tool_call，第二次返回最终答复（自循环）。"""

        def bind_tools(self, tools, **kwargs):
            return self

    calls = []

    @tool
    async def echo_tool(text: str) -> str:
        """将传入文本原样返回（端到端工具调用测试用）。"""
        calls.append(text)
        return f"echo:{text}"

    model = ToolCallingFakeModel(responses=[
        AIMessage(content="", tool_calls=[
            {"name": "echo_tool", "args": {"text": "你好世界"}, "id": "call_1"},
        ]),
        AIMessage(content="我已经调用了工具，回答完毕。"),
    ])
    monkeypatch.setattr(agent_module.agent_factory, "_create_chat_model", lambda custom_model=None: model)

    result = await get_agent_response("请确认工具调用链路", custom_tools=[echo_tool], user_id="u1")

    assert result["response"] == "我已经调用了工具，回答完毕。"
    assert calls == ["你好世界"]  # 真实工具确实被执行
    assert any(step["tool"] == "echo_tool" for step in result["steps"])