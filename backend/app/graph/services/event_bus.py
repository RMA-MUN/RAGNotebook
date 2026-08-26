"""per-user 抽取事件总线：进程内 asyncio.Queue 发布/订阅，供 /api/graph/events 长连接使用。"""
import asyncio
from collections import defaultdict


class GraphEventBus:
    def __init__(self):
        self._subscribers: dict[str, set[asyncio.Queue]] = defaultdict(set)
        self._lock = asyncio.Lock()

    async def subscribe(self, user_id: str) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        async with self._lock:
            self._subscribers[user_id].add(q)
        return q

    async def unsubscribe(self, user_id: str, q: asyncio.Queue) -> None:
        async with self._lock:
            self._subscribers[user_id].discard(q)

    async def publish(self, user_id: str, event: dict) -> None:
        async with self._lock:
            queues = list(self._subscribers.get(user_id, ()))
        for q in queues:
            q.put_nowait(event)


event_bus = GraphEventBus()