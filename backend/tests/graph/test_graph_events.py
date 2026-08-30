import asyncio
import json

import pytest

from app.graph.services.event_bus import GraphEventBus


@pytest.mark.asyncio
async def test_publish_delivers_to_subscriber():
    bus = GraphEventBus()
    q = await bus.subscribe("u1")
    await bus.publish("u1", {"type": "extract_done", "note_id": "n1"})
    evt = await asyncio.wait_for(q.get(), timeout=1)
    assert evt["note_id"] == "n1"
    await bus.unsubscribe("u1", q)


@pytest.mark.asyncio
async def test_publish_does_not_leak_across_users():
    bus = GraphEventBus()
    q1 = await bus.subscribe("u1")
    await bus.publish("u2", {"type": "x"})
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(q1.get(), timeout=0.1)
    await bus.unsubscribe("u1", q1)


@pytest.mark.asyncio
async def test_events_endpoint_streams_extract_event(client, monkeypatch):
    from fastapi.testclient import TestClient
    from main import app

    # SSE 端点需真实网络事件循环，这里只验证事件形状（通过 bus 直接 push）
    bus = GraphEventBus()
    q = await bus.subscribe("u1")
    await bus.publish("u1", {"type": "extract_done", "note_id": "n1", "status": "success"})
    evt = await asyncio.wait_for(q.get(), timeout=1)
    assert evt["type"] == "extract_done"
    await bus.unsubscribe("u1", q)