"""图谱 API 路由。"""
import asyncio
import json

from fastapi import Depends
from fastapi.responses import StreamingResponse
from fastapi.routing import APIRouter

from app.graph.services.event_bus import event_bus
from app.utils.auth_utils import get_current_user_id

graph_router = APIRouter(prefix="/api/graph", tags=["graph"])


@graph_router.get("/events")
async def graph_events(user_id: str = Depends(get_current_user_id)):
    """SSE 长连接订阅：抽取进度/结果实时推送（fetch ReadableStream 消费，带 JWT）。"""
    q = await event_bus.subscribe(user_id)

    async def gen():
        try:
            yield "retry: 3000\n\n"
            while True:
                try:
                    event = await asyncio.wait_for(q.get(), timeout=15.0)
                    yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                except asyncio.TimeoutError:
                    yield "data: {\"type\":\"ping\"}\n\n"
        finally:
            await event_bus.unsubscribe(user_id, q)

    return StreamingResponse(gen(), media_type="text/event-stream")