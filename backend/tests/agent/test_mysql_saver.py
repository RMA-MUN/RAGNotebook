"""MySQLCheckpointSaver 单测：注入 SQLite session_factory，验证 langgraph saver 语义。

覆盖：aput/aget_tuple roundtrip、parent 链、aput_writes 与 pending_writes、
alist 排序与 limit、adelete_thread、中断写(WRITES_IDX_MAP 下标)。
"""

import pytest
from langchain_core.messages import HumanMessage
from langgraph.checkpoint.base import CheckpointTuple

from app.agent.checkpoint.mysql_saver import MySQLCheckpointSaver


def _config(thread_id: str, checkpoint_id: str | None = None) -> dict:
    cfg = {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}}
    if checkpoint_id:
        cfg["configurable"]["checkpoint_id"] = checkpoint_id
    return cfg


def _make_checkpoint(cid: str) -> dict:
    return {
        "v": 1,
        "id": cid,
        "ts": "2026-09-07T00:00:00+00:00",
        "channel_values": {"messages": [HumanMessage(content="hi")]},
        "channel_versions": {},
        "versions_seen": {},
        "pending_sends": [],
        "updated_channels": None,
    }


@pytest.fixture
def saver(session_factory):
    return MySQLCheckpointSaver(session_factory)


async def test_aput_aget_tuple_roundtrip(saver):
    cfg = _config("thread-1")
    saved = await saver.aput(cfg, _make_checkpoint("c1"), {"step": 0}, {})
    assert saved["configurable"]["checkpoint_id"] == "c1"

    tup = await saver.aget_tuple(_config("thread-1"))
    assert isinstance(tup, CheckpointTuple)
    assert tup.checkpoint["id"] == "c1"
    assert tup.metadata["step"] == 0
    assert tup.pending_writes is None or tup.pending_writes == []
    # 反序列化后的消息可读
    msgs = tup.checkpoint["channel_values"]["messages"]
    assert msgs[0].content == "hi"


async def test_aget_tuple_by_checkpoint_id_and_parent_chain(saver):
    await saver.aput(_config("thread-1"), _make_checkpoint("c1"), {"step": 0}, {})
    # 第二次写入以 c1 为父：config 携带 checkpoint_id=c1
    await saver.aput(_config("thread-1", "c1"), _make_checkpoint("c2"), {"step": 1}, {})
    tup = await saver.aget_tuple(_config("thread-1", "c2"))
    assert tup.parent_config["configurable"]["checkpoint_id"] == "c1"


async def test_aput_writes_roundtrip(saver):
    await saver.aput(_config("thread-1"), _make_checkpoint("c1"), {"step": 0}, {})
    cfg = _config("thread-1", "c1")
    await saver.aput_writes(cfg, [("messages", HumanMessage(content="tool out"))], "task-1")

    tup = await saver.aget_tuple(_config("thread-1", "c1"))
    assert tup.pending_writes is not None
    assert tup.pending_writes[0] == ("task-1", "messages", HumanMessage(content="tool out"))


async def test_alist_newest_first_with_limit(saver):
    for cid in ("c1", "c2", "c3"):
        await saver.aput(_config("thread-1"), _make_checkpoint(cid), {"step": 0}, {})
    rows = [t async for t in saver.alist(_config("thread-1"), limit=2)]
    assert [r.checkpoint["id"] for r in rows] == ["c3", "c2"]


async def test_adelete_thread_removes_checkpoints_and_writes(saver):
    await saver.aput(_config("thread-1"), _make_checkpoint("c1"), {"step": 0}, {})
    await saver.aput_writes(_config("thread-1", "c1"), [("messages", "x")], "task-1")
    await saver.adelete_thread("thread-1")
    assert await saver.aget_tuple(_config("thread-1")) is None
    rows = [t async for t in saver.alist(_config("thread-1"))]
    assert rows == []


async def test_special_write_uses_writes_idx_map_index(saver):
    """interrupt 类特殊写必须以 WRITES_IDX_MAP 负下标落库（langgraph 依赖）。"""

    await saver.aput(_config("thread-1"), _make_checkpoint("c1"), {"step": 0}, {})
    cfg = _config("thread-1", "c1")
    await saver.aput_writes(cfg, [("__interrupt__", ("node", {"decision": "ask"}))], "task-1")
    # 以上走 REPLACE 分支（channel 在 WRITES_IDX_MAP 中）

    tup = await saver.aget_tuple(_config("thread-1", "c1"))
    assert tup.pending_writes[0][1] == "__interrupt__"
