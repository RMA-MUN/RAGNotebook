"""共享测试替身（in-memory fakes），供整个测试套件使用。

统一策略：MySQL / Redis / LLM / 重排序模型全部用内存替身替换，
测试不依赖任何外部服务，可在任意环境直接运行。
"""
import asyncio
import fnmatch
import itertools
import uuid as uuidlib

from langchain_core.documents import Document
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage

TEST_USER_ID = "test-user-id"


class FakeRedis:
    """redis.asyncio.Redis 的内存替身（覆盖业务代码用到的子集）。"""

    def __init__(self):
        self._data = {}
        self._expire = {}

    async def ping(self):
        return True

    async def get(self, key):
        return self._data.get(str(key))

    async def set(self, key, value, ex=None):
        self._data[str(key)] = value
        if ex is not None:
            self._expire[str(key)] = ex

    async def setex(self, key, ttl, value):
        self._data[str(key)] = value
        self._expire[str(key)] = ttl

    async def incr(self, key):
        k = str(key)
        self._data[k] = int(self._data.get(k, 0)) + 1
        return self._data[k]

    async def delete(self, *keys):
        removed = 0
        for key in keys:
            k = str(key)
            if k in self._data:
                del self._data[k]
                self._expire.pop(k, None)
                removed += 1
        return removed

    async def keys(self, pattern="*"):
        return [k for k in self._data if fnmatch.fnmatch(k, pattern)]

    # ---- 测试辅助 ----
    def clear(self):
        self._data.clear()
        self._expire.clear()


async def install_fake_redis(monkeypatch, redis: FakeRedis | None = None):
    """把 redis 相关入口全部替换为内存替身（含各模块直接 import 的引用）。"""
    redis = redis or FakeRedis()

    async def _connect_redis():
        return redis

    # 模块内直接 import connect_redis 的位置
    targets = [
        "app.db.redis_config.connect_redis",
        "app.utils.auth_utils.connect_redis",
        "app.router.user._connect_redis",
        "app.core.rate_limit.connect_redis",
        "app.cache.redis_decorator.connect_redis",
    ]
    for target in targets:
        monkeypatch.setattr(target, _connect_redis)
    return redis


class FakeVectorStoreService:
    """VectorStoreService 内存替身（知识库文档服务）。

    route_score 控制路由层是否触发 RAG 前置管线（>0.5 触发）。
    """

    def __init__(self, route_score: float = 0.0, documents: list[Document] | None = None):
        self.route_score = route_score
        self._hybrid_documents = documents or []
        self.md5_store = None

    async def compute_route_score(self, query: str, user_id: str) -> float:
        return self.route_score

    def get_all_documents(self):
        return list(self._hybrid_documents)


class FakeReorderService:
    """ReorderService 内存替身：原样返回文档并附默认相似度。"""

    def __init__(self, success: bool = True):
        self.success = success

    async def reorder_documents(self, query: str, documents: list, thinking_callback=None) -> dict:
        if not self.success:
            return {"success": False, "error": "fake 重排序失败", "documents": []}
        return {
            "success": True,
            "documents": [
                {"document": doc, "similarity": 1.0 - i * 0.01}
                for i, doc in enumerate(documents)
            ],
        }


def make_fake_chat_model(responses: list[str] | None = None):
    """构造一个可无限调用的假 ChatModel（ainvoke/astream 均返回预设文案）。

    使用 langchain_core 自带的 GenericFakeChatModel，因此可在 LCEL 链（|）中使用。
    """
    responses = responses or ["这是假模型的预设回答。"]
    messages = itertools.cycle([AIMessage(content=resp) for resp in responses])
    return GenericFakeChatModel(messages=iter(messages))


class FakeAgent:
    """create_agent 返回的 CompiledStateGraph 内存替身。

    - ainvoke：直接返回预设 messages 状态（非流式路径）。
    - astream_events：按序产出预设事件（流式路径，v2 事件流）。
    用于在编排层测试 get_agent_response / get_agent_stream_response，
    避免触发 LangChain 真实的 agent 规划循环。
    """

    def __init__(self, messages: list | None = None, events: list[dict] | None = None):
        self.messages = messages or [AIMessage(content="这是假 Agent 的回答。")]
        self.events = events or []
        self.inputs = []

    async def ainvoke(self, inputs: dict):
        self.inputs.append(inputs)
        return {"messages": self.messages}

    async def astream_events(self, inputs: dict, version: str = "v2"):
        self.inputs.append(inputs)
        for event in self.events:
            yield event