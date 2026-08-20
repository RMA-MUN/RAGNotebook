"""编排层测试：AgentFactory / get_agent_response / get_agent_stream_response。

Level A（推荐）：monkeypatch `agent_factory.create_agent_executor` 返回
`FakeAgentExecutor`，测试编排逻辑（历史构建、chunk 拼接、异常回退、SSE 帧序列、
会话写入）。
Level C（尽力而为的真端到端）：不 mock create_agent_executor，给工厂注入一个
能发出真实 tool_call AIMessage 的假模型，让 create_tool_calling_agent + AgentExecutor
真正调用一次工具。
"""
import json

import pytest_asyncio
from langchain_classic.agents import AgentExecutor
from langchain_core.agents import AgentAction
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.tools import tool
from sqlalchemy import select

import app.services as services_module
from app.agent import agent as agent_module
from app.agent.agent import (
    AgentFactory,
    get_agent_executor,
    get_agent_response,
    get_agent_stream_response,
)
from app.models.chat_history import ChatMessage, ChatSession
from tests.conftest import patch_session_factory
from tests.fakes import FakeAgentExecutor


# ---------------------------------------------------------------------------
# 本地 fixtures / 替身
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


class RecordingExecutor(FakeAgentExecutor):
    """记录每次 astream 收到的输入，并可额外注入非 output chunk。"""

    def __init__(self, outputs=None, extra_chunks=None):
        super().__init__(outputs)
        self.extra_chunks = extra_chunks or []
        self.inputs = []

    async def astream(self, inputs):
        self.inputs.append(inputs)
        for chunk in self.extra_chunks:
            yield chunk
        for out in self.outputs:
            yield {"output": out}


class StepsOnlyExecutor:
    """只产出传入 chunk、完全不产出 output chunk 的替身 executor。"""

    def __init__(self, chunks):
        self.chunks = chunks

    async def astream(self, inputs):
        for chunk in self.chunks:
            yield chunk


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


def test_get_agent_executor_returns_agent_executor(monkeypatch):
    """真实工厂路径：给定 KEY 后能构造出 AgentExecutor（不真正调用 LLM）。"""
    monkeypatch.setenv("OPENAI_API_KEY", "dummy-key-for-construction-test")
    executor = get_agent_executor()
    assert isinstance(executor, AgentExecutor)


async def test_agent_factory_injects_custom_tools(monkeypatch):
    """custom_tools 会绕过默认工具列表（用假 executor 观察 tools 参数）。"""
    seen = {}

    class CapturingExecutor(FakeAgentExecutor):
        async def astream(self, inputs):
            yield {"output": "ok"}

    def fake_factory(custom_tools=None, **kwargs):
        seen["tools"] = custom_tools
        return CapturingExecutor()

    @tool
    def dummy_tool(x: str) -> str:
        """占位工具（不真正使用）。"""
        return x

    monkeypatch.setattr(agent_module.agent_factory, "create_agent_executor", fake_factory)
    my_tools = [dummy_tool]
    await get_agent_response("你好", custom_tools=my_tools, user_id="u1")
    assert seen["tools"] is my_tools


# ---------------------------------------------------------------------------
# get_agent_response（Level A）
# ---------------------------------------------------------------------------
async def test_get_agent_response_joins_outputs(monkeypatch):
    fake = FakeAgentExecutor(outputs=["第一段", "第二段"])
    monkeypatch.setattr(agent_module.agent_factory, "create_agent_executor", lambda **kw: fake)

    result = await get_agent_response("你好", user_id="u1")
    assert result["response"] == "第一段第二段"
    assert result["steps"] == []


async def test_get_agent_response_fallback_when_no_output(monkeypatch):
    # 出现 intermediate_steps 但从未产出 output chunk
    action = AgentAction(tool="what_time_is_now", tool_input={}, log="看看时间")
    fake = StepsOnlyExecutor([{"intermediate_steps": [(action, "某个时间")]}])
    monkeypatch.setattr(agent_module.agent_factory, "create_agent_executor", lambda **kw: fake)

    result = await get_agent_response("你好", user_id="u1")
    assert result["response"] == "抱歉，我无法理解您的请求。"


async def test_get_agent_response_history_building(monkeypatch):
    fake = RecordingExecutor(outputs=["答复"])
    monkeypatch.setattr(agent_module.agent_factory, "create_agent_executor", lambda **kw: fake)

    history = [("用户问题", "助手回答")]
    result = await get_agent_response("新问题", history=history, user_id="u1")

    assert result["response"] == "答复"
    chat_history = fake.inputs[0]["chat_history"]
    assert len(chat_history) == 2
    assert isinstance(chat_history[0], HumanMessage)
    assert chat_history[0].content == "用户问题"
    assert isinstance(chat_history[1], AIMessage)
    assert chat_history[1].content == "助手回答"
    assert fake.inputs[0]["system_prompt"] == agent_module.agent_factory.default_system_prompt


async def test_get_agent_response_collects_steps(monkeypatch):
    action = AgentAction(tool="search_notes_tool", tool_input={"query": "测试"}, log="先搜索笔记")
    fake = RecordingExecutor(
        outputs=["最终回答"],
        extra_chunks=[{"intermediate_steps": [(action, "搜索结果内容")]}],
    )
    monkeypatch.setattr(agent_module.agent_factory, "create_agent_executor", lambda **kw: fake)

    result = await get_agent_response("帮我找笔记", user_id="u1")
    assert result["response"] == "最终回答"
    assert len(result["steps"]) == 1
    step = result["steps"][0]
    assert step["thought"] == "先搜索笔记"
    assert step["tool"] == "search_notes_tool"
    assert step["tool_input"] == {"query": "测试"}
    assert step["tool_output"] == "搜索结果内容"


async def test_get_agent_response_exception_fallback(monkeypatch):
    def boom(**kwargs):
        raise RuntimeError("模型连接失败")

    monkeypatch.setattr(agent_module.agent_factory, "create_agent_executor", boom)

    result = await get_agent_response("你好", user_id="u1")
    assert result["response"] == "抱歉，处理您的请求时出现了错误: 模型连接失败"
    assert result["steps"] == []


# ---------------------------------------------------------------------------
# get_agent_stream_response（Level A）
# ---------------------------------------------------------------------------
async def test_get_agent_stream_response_full_flow(monkeypatch, patched_db, fresh_session_manager):
    response_text = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"  # 36 字符 -> 15/15/6 三分片
    fake = FakeAgentExecutor(outputs=[response_text])
    monkeypatch.setattr(agent_module.agent_factory, "create_agent_executor", lambda **kw: fake)

    frames = await _collect_stream("你好", session_id="s1", user_id="u1")
    events = _parse_frames(frames)

    # 1) 首帧：空内容 response + session_id
    assert events[0]["type"] == "response"
    assert events[0]["content"] == ""
    assert events[0]["session_id"] == "s1"

    # 2) 无 error 帧
    assert all(e["type"] != "error" for e in events)

    # 3) response 分片拼接 == 完整回答（chunk_size=15）
    assert "".join(e["content"] for e in events if e["type"] == "response" and e["content"]) == response_text
    assert [e["content"] for e in events if e["type"] == "response" and e["content"]] == [
        "0123456789ABCDE", "FGHIJKLMNOPQRST", "UVWXYZ",
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


async def test_get_agent_stream_response_agent_error(monkeypatch, patched_db, fresh_session_manager):
    class BrokenExecutor:
        async def astream(self, inputs):
            if False:  # pragma: no cover
                yield None
            raise RuntimeError("代理内部异常")

    monkeypatch.setattr(agent_module.agent_factory, "create_agent_executor", lambda **kw: BrokenExecutor())

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

    fake = RecordingExecutor(outputs=["新回答"])
    monkeypatch.setattr(agent_module.agent_factory, "create_agent_executor", lambda **kw: fake)

    frames = await _collect_stream("新问题", session_id="s1", user_id="u1")
    events = _parse_frames(frames)
    assert events[-1]["type"] == "done"

    # run_agent 先从 DB 加载历史并构造 Human/AI 消息对
    chat_history = fake.inputs[0]["chat_history"]
    assert [type(m).__name__ for m in chat_history] == ["HumanMessage", "AIMessage"]
    assert [m.content for m in chat_history] == ["旧问题", "旧回答"]

    # add_message 又在末尾追加了新的 user/assistant 对
    async with patched_db() as db:
        msgs = (await db.execute(
            select(ChatMessage).where(ChatMessage.session_id == "s1").order_by(ChatMessage.id)
        )).scalars().all()
    assert [m.content for m in msgs] == ["旧问题", "旧回答", "新问题", "新回答"]


async def test_get_agent_stream_response_with_rag_context(monkeypatch, patched_db, fresh_session_manager):
    fake = RecordingExecutor(outputs=["基于资料的回答"])
    monkeypatch.setattr(agent_module.agent_factory, "create_agent_executor", lambda **kw: fake)

    frames = await _collect_stream("问题", session_id="s1", user_id="u1", rag_context="RAG参考资料内容")
    events = _parse_frames(frames)
    assert events[-1]["type"] == "done"

    system_prompt = fake.inputs[0]["system_prompt"]
    assert "RAG参考资料内容" in system_prompt
    assert "参考资料" in system_prompt


# ---------------------------------------------------------------------------
# Level C（尽力而为的真端到端）
# ---------------------------------------------------------------------------
async def test_level_c_end_to_end_real_tool_calling(monkeypatch):
    """不 mock create_agent_executor：真实 create_tool_calling_agent + AgentExecutor，
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
