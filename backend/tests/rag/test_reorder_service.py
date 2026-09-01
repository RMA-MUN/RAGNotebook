"""reorder_service.py — ReorderService 云端 rerank 逻辑测试（不发真实网络请求）。"""
import pytest

from app.rag.reorder_service import ReorderService, get_reorder_config_for_user


class FakeAsyncClient:
    """假 httpx.AsyncClient：post 返回预设响应或抛异常。"""

    def __init__(self, payload=None, exc: Exception | None = None, requests: list | None = None):
        self.payload = payload
        self.exc = exc
        self.requests = requests if requests is not None else []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, **kwargs):
        self.requests.append({"url": url, **kwargs})
        if self.exc is not None:
            raise self.exc

        class _Resp:
            def __init__(self, payload):
                self._payload = payload

            def raise_for_status(self):
                pass

            def json(self):
                return self._payload

        return _Resp(self.payload)


def _make_service(payload=None, exc: Exception | None = None) -> tuple[ReorderService, FakeAsyncClient]:
    """构造走假 HTTP 客户端的服务实例，返回 (service, client)。"""
    client = FakeAsyncClient(payload=payload, exc=exc)
    service = ReorderService(http_client_factory=lambda **kw: client)
    return service, client


def _rerank_payload(scores: list[float]) -> dict:
    return {"results": [{"index": i, "relevance_score": s} for i, s in enumerate(scores)]}


class TestReorderDocuments:
    async def test_success_returns_sorted_documents(self):
        service, client = _make_service(payload=_rerank_payload([0.3, 0.9, 0.5]))
        result = await service.reorder_documents("查询", ["docA", "docB", "docC"])
        assert result["success"] is True
        assert result["error"] == ""
        docs = result["documents"]
        # 按相似度降序排列
        assert [d["document"] for d in docs] == ["docB", "docC", "docA"]
        assert [d["similarity"] for d in docs] == [0.9, 0.5, 0.3]
        for d in docs:
            assert set(d.keys()) == {"document", "similarity"}

    async def test_request_carries_model_query_documents(self):
        service, client = _make_service(payload=_rerank_payload([0.5]))
        service.api_base_url = "https://api.example.com/v1"
        service.api_key = "sk-test"
        service.model = "test-reranker"
        await service.reorder_documents("q", ["a"])
        req = client.requests[0]
        assert req["url"] == "https://api.example.com/v1/rerank"
        assert req["headers"]["Authorization"] == "Bearer sk-test"
        assert req["json"] == {"model": "test-reranker", "query": "q", "documents": ["a"]}

    async def test_success_with_thinking_callback(self):
        calls = []

        async def cb(data):
            calls.append(data)

        service, _ = _make_service(payload=_rerank_payload([0.8, 0.2]))
        result = await service.reorder_documents("q", ["a", "b"], thinking_callback=cb)
        assert result["success"] is True
        # 两次 thinking：开始计算 + 完成详情
        assert len(calls) == 2
        assert calls[0]["stage"] == "reorder"
        assert "2" in calls[0]["content"]
        assert calls[1]["details"]["scores"][0]["score"] == 0.8

    async def test_empty_documents_short_circuits(self):
        service, client = _make_service(payload=_rerank_payload([]))
        result = await service.reorder_documents("q", [])
        assert result == {"success": True, "documents": [], "error": ""}
        assert client.requests == []  # 空列表不发起请求

    async def test_api_failure_returns_error_shape(self):
        service, _ = _make_service(exc=RuntimeError("rerank 服务不可用"))
        result = await service.reorder_documents("q", ["a", "b"])
        assert result["success"] is False
        assert result["documents"] == []
        assert result["error"] == "rerank 服务不可用"

    async def test_no_callback_when_none(self):
        service, _ = _make_service(payload=_rerank_payload([1.0]))
        result = await service.reorder_documents("q", ["a"])
        assert result["success"] is True


class TestFormatReorderResult:
    async def test_formats_scores_and_content(self):
        text = await ReorderService.format_reorder_result(
            [{"document": "内容A", "similarity": 0.9123}]
        )
        assert "0.9123" in text
        assert "内容A" in text


class TestGetReorderConfigForUser:
    async def test_falls_back_to_global_defaults_when_db_fails(self, monkeypatch):
        """DB 查询异常时 fail-soft：返回全局 RERANKER_* 默认，而不是抛错。"""
        from app.core.settings import settings

        monkeypatch.setattr(settings, "RERANKER_API_BASE_URL", "https://rerank.example.com/v1/")
        monkeypatch.setattr(settings, "RERANKER_API_KEY", "global-key")
        monkeypatch.setattr(settings, "RERANKER_MODEL", "global-model")

        async def _boom(user_id):
            raise RuntimeError("db down")

        monkeypatch.setattr("app.utils.user_config.get_user_ai_config", _boom)

        cfg = await get_reorder_config_for_user("user-1")

        assert cfg == {
            "base_url": "https://rerank.example.com/v1",
            "api_key": "global-key",
            "model": "global-model",
        }
