"""Level C：真实 create_agent + SQLite saver + 工具调用假模型的完整流式链路。

验证：interrupt 帧 → resume(approve) → 工具真正执行 → done → 镜像落库；
reject 后工具不执行。
"""
import json

import pytest_asyncio
from langchain.agents import create_agent
from langchain.agents.middleware import HumanInTheLoopMiddleware
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, ToolCall
from langchain_core.tools import tool
from sqlalchemy import select

import app.agent.agent as agent_module
from app.agent.agent import (
    get_agent_resume_stream_response,
    get_agent_stream_response,
    read_approval_snapshot,
)
from app.agent.checkpoint.mysql_saver import MySQLCheckpointSaver
from app.models.chat_history import ChatMessage, ChatSession
from tests.conftest import patch_session_factory

TOOL_CALLS: list[tuple] = []


@tool
def create_note_tool(title: str, content: str = "") -> str:
    """创建笔记（集成测试用真实工具）。"""
    TOOL_CALLS.append(("create_note_tool", title))
    return f"已创建 {title}"


@tool
def read_time_tool() -> str:
    """读取时间（非白名单，自动放行）。"""
    TOOL_CALLS.append(("read_time_tool",))
    return "2026-09-07"


class ToolCallingApprovalModel(FakeMessagesListChatModel):
    """首轮 tool_call(create_note_tool)，恢复后第二轮最终答复。"""

    def bind_tools(self, tools, **kwargs):
        return self


@pytest_asyncio.fixture
async def real_agent_env(monkeypatch, session_factory):
    """真实图 + SQLite 版 MySQL saver；工厂替换为真 create_agent。"""
    patch_session_factory(monkeypatch, session_factory)
    saver = MySQLCheckpointSaver(session_factory=session_factory)
    monkeypatch.setattr(agent_module, "get_checkpointer", lambda: saver)

    model = ToolCallingApprovalModel(responses=[
        AIMessage(content="", tool_calls=[
            ToolCall(id="call-1", name="create_note_tool", args={"title": "测试"}),
        ]),
        AIMessage(content="笔记创建成功"),
    ])

    def real_create_agent(custom_tools=None, custom_system_prompt=None, **kwargs):
        return create_agent(
            model,
            custom_tools or [create_note_tool, read_time_tool],
            system_prompt=custom_system_prompt,
            middleware=[HumanInTheLoopMiddleware(interrupt_on={
                "create_note_tool": {"allowed_decisions": ["approve", "reject"]},
            })],
            checkpointer=saver,
        )

    monkeypatch.setattr(agent_module.agent_factory, "create_agent", real_create_agent)
    monkeypatch.setattr(agent_module.agent_factory, "default_system_prompt", "你是助手")
    yield {"saver": saver, "session_factory": session_factory}


async def _collect_stream(*args, **kwargs):
    return [f async for f in get_agent_stream_response(*args, **kwargs)]


def _parse(raw_frames):
    return [json.loads(f[len("data: "):].rstrip("\n")) for f in raw_frames]


async def test_stream_approve_full_flow(real_agent_env):
    TOOL_CALLS.clear()
    frames = await _collect_stream("帮我创建笔记《测试》", session_id="s1", user_id="u1")
    events = _parse(frames)
    interrupt_events = [e for e in events if e["type"] == "interrupt"]
    assert len(interrupt_events) == 1
    run_id = interrupt_events[0]["run_id"]
    payload = interrupt_events[0]["payload"]
    assert [a["name"] for a in payload["action_requests"]] == ["create_note_tool"]
    assert TOOL_CALLS == []  # 未批准前工具绝不能执行
    assert events[-1]["type"] == "interrupt"  # 无 done 帧，不落镜像

    async with real_agent_env["session_factory"]() as db:
        sess = (await db.execute(
            select(ChatSession).where(ChatSession.id == "s1"))).scalar_one()
        assert sess.pending_run_id == run_id
        msgs = (await db.execute(
            select(ChatMessage).where(ChatMessage.session_id == "s1"))).scalars().all()
        assert msgs == []

    resume_frames = []
    async for frame in get_agent_resume_stream_response(
        session_id="s1", user_id="u1", run_id=run_id,
        decisions=[{"type": "approve"}],
    ):
        resume_frames.append(frame)
    resume_events = _parse(resume_frames)
    assert resume_events[-1]["type"] == "done"
    assert TOOL_CALLS == [("create_note_tool", "测试")]

    async with real_agent_env["session_factory"]() as db:
        sess = (await db.execute(
            select(ChatSession).where(ChatSession.id == "s1"))).scalar_one()
        assert sess.pending_run_id == run_id
        msgs = (await db.execute(
            select(ChatMessage).where(ChatMessage.session_id == "s1"))).scalars().all()
        assert [m.role for m in msgs] == ["user", "assistant"]
        assert [m.content for m in msgs] == ["帮我创建笔记《测试》", "笔记创建成功"]
    # 审批 thread 被保留（供查看审批前快照），快照标记已完成
    assert await real_agent_env["saver"].aget_tuple(
        {"configurable": {"thread_id": run_id}}) is not None
    snap = await read_approval_snapshot(run_id)
    assert snap is not None
    assert snap["active"] is False
    assert snap["query"] == "帮我创建笔记《测试》"
    assert [a["name"] for a in snap["payload"]["action_requests"]] == ["create_note_tool"]
    # 同一 run 再次 resume → ALREADY_COMPLETED，不写重复镜像
    resume2_frames = []
    async for frame in get_agent_resume_stream_response(
        session_id="s1", user_id="u1", run_id=run_id,
        decisions=[{"type": "approve"}],
    ):
        resume2_frames.append(frame)
    resume2_events = _parse(resume2_frames)
    assert [e for e in resume2_events if e["type"] == "error"][0]["content"] == "ALREADY_COMPLETED"
    assert TOOL_CALLS == [("create_note_tool", "测试")]
    async with real_agent_env["session_factory"]() as db:
        msgs = (await db.execute(
            select(ChatMessage).where(ChatMessage.session_id == "s1"))).scalars().all()
        assert len(msgs) == 2


async def test_stream_reject_full_flow(real_agent_env):
    TOOL_CALLS.clear()
    frames = await _collect_stream("创建笔记", session_id="s2", user_id="u1")
    interrupt_events = [e for e in _parse(frames) if e["type"] == "interrupt"]
    assert len(interrupt_events) == 1
    run_id = interrupt_events[0]["run_id"]

    resume_frames = []
    async for frame in get_agent_resume_stream_response(
        session_id="s2", user_id="u1", run_id=run_id,
        decisions=[{"type": "reject", "message": "不需要了"}],
    ):
        resume_frames.append(frame)
    assert _parse(resume_frames)[-1]["type"] == "done"
    assert TOOL_CALLS == []  # reject 后工具不执行

    async with real_agent_env["session_factory"]() as db:
        sess = (await db.execute(
            select(ChatSession).where(ChatSession.id == "s2"))).scalar_one()
        assert sess.pending_run_id == run_id
    # reject 的审批 thread 同样保留，快照可查
    assert await real_agent_env["saver"].aget_tuple(
        {"configurable": {"thread_id": run_id}}) is not None
    snap = await read_approval_snapshot(run_id)
    assert snap is not None and snap["active"] is False
