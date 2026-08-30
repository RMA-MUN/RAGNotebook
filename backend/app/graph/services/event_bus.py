"""per-user 抽取事件总线：进程内 asyncio.Queue 发布/订阅，供 /api/graph/events 长连接使用。"""
import asyncio
from collections import defaultdict


class GraphEventBus:
    """per-user 事件分发：subscribe 返回独立队列，publish 向该用户全部队列投递。

    队列无界且 put_nowait，事件量为个位数/任务不会堆积；订阅端断开（SSE 关闭）
    必须 unsubscribe，否则队列泄漏。
    """

    def __init__(self):
        self._subscribers: dict[str, set[asyncio.Queue]] = defaultdict(set)
        self._lock = asyncio.Lock()

    async def subscribe(self, user_id: str) -> asyncio.Queue:
        """注册订阅，返回接收事件的新队列（SSE 长连接持有）。"""
        q: asyncio.Queue = asyncio.Queue()
        async with self._lock:
            self._subscribers[user_id].add(q)
        return q

    async def unsubscribe(self, user_id: str, q: asyncio.Queue) -> None:
        """注销订阅；队列由调用方废弃。"""
        async with self._lock:
            self._subscribers[user_id].discard(q)

    async def publish(self, user_id: str, event: dict) -> None:
        """向该用户所有订阅队列投递事件（无订阅者时静默丢弃）。"""
        async with self._lock:
            queues = list(self._subscribers.get(user_id, ()))
        for q in queues:
            q.put_nowait(event)


event_bus = GraphEventBus()