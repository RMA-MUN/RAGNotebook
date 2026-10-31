"""HumanInTheLoopMiddleware + create_agent + MemorySaver 的图层行为基线。

spike 已验证的真实语义：
- 只有 interrupt_on 白名单工具进入 action_requests；其余工具自动放行；
- approve 后白名单与非白名单工具在同一轮一起执行；
- reject 后白名单工具不执行，模型收到 status="error" 的 ToolMessage；
- 假模型响应按调用次序逐条消费（两次独立 astream_events 各消费一条）。
"""
import pytest
from langchain.agents import create_agent
from langchain.agents.middleware import HumanInTheLoopMiddleware
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolCall, ToolMessage
from langchain_core.tools import tool
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

EXECUTED: list[tuple] = []


@tool
def create_note_tool(title: str) -> str:
    """创建笔记（白名单工具）。"""
    EXECUTED.append(("create_note_tool", title))
    return f"created {title}"


@tool
def read_time_tool() -> str:
    """读取时间（非白名单工具，自动放行）。"""
    EXECUTED.append(("read_time_tool",))
    return "now"


class ToolCallingFakeModel(FakeMessagesListChatModel):
    """按调用次序逐条返回预设消息（覆盖 bind_tools 默认 NotImplementedError）。"""

    def bind_tools(self, tools, **kwargs):
        return self


def _build(model, saver):
    return create_agent(
        model,
        [create_note_tool, read_time_tool],
        middleware=[HumanInTheLoopMiddleware(interrupt_on={
            "create_note_tool": {"allowed_decisions": ["approve", "reject"]},
        })],
        checkpointer=saver,
    )


def _first_ai_tool_call_message():
    return AIMessage(content="", tool_calls=[
        ToolCall(id="c1", name="read_time_tool", args={}),
        ToolCall(id="c2", name="create_note_tool", args={"title": "t"}),
    ])


@pytest.mark.asyncio
async def test_approve_executes_whitelist_and_auto_approved_tools():
    EXECUTED.clear()
    saver = MemorySaver()
    model = ToolCallingFakeModel(responses=[
        _first_ai_tool_call_message(),
        AIMessage(content="approve final answer"),
    ])
    cfg = {"configurable": {"thread_id": "approve-case"}}

    await _build(model, saver).ainvoke({"messages": [HumanMessage("hi")]}, cfg)
    snap = await _build(model, saver).aget_state(cfg)
    assert snap.next  # 执行在中断点挂起
    assert len(snap.interrupts) == 1
    reqs = snap.interrupts[0].value["action_requests"]
    # 只有白名单工具被拦截；同批的 read_time_tool 不在其中
    assert [r["name"] for r in reqs] == ["create_note_tool"]
    assert EXECUTED == []  # 未批准前任何工具都不执行

    await _build(model, saver).ainvoke(
        Command(resume={"decisions": [{"type": "approve"}]}), cfg)
    # 自动放行的 read_time 与批准的 create_note 同一轮执行
    assert EXECUTED == [("read_time_tool",), ("create_note_tool", "t")]
    last = (await _build(model, saver).aget_state(cfg)).values["messages"][-1]
    assert last.content == "approve final answer"


@pytest.mark.asyncio
async def test_reject_skips_tool_and_delivers_error_tool_message():
    EXECUTED.clear()
    saver = MemorySaver()
    model = ToolCallingFakeModel(responses=[
        _first_ai_tool_call_message(),
        AIMessage(content="reject final answer"),
    ])
    cfg = {"configurable": {"thread_id": "reject-case"}}

    await _build(model, saver).ainvoke({"messages": [HumanMessage("hi")]}, cfg)
    await _build(model, saver).ainvoke(
        Command(resume={"decisions": [{"type": "reject", "message": "不需要"}]}),
        cfg,
    )
    # create_note 被拒不执行；read_time 自动放行仍执行
    assert EXECUTED == [("read_time_tool",)]

    msgs = (await _build(model, saver).aget_state(cfg)).values["messages"]
    error_msgs = [m for m in msgs
                  if isinstance(m, ToolMessage) and m.status == "error"]
    assert len(error_msgs) == 1
    assert error_msgs[0].tool_call_id == "c2"
    assert error_msgs[0].content == "不需要"
    assert msgs[-1].content == "reject final answer"
