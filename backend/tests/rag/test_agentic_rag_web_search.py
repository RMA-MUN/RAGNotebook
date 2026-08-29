import httpx
import pytest

from app.rag.agentic_rag.web_search import WebSearchClient


@pytest.fixture(autouse=True)
def _clean_web_search_env(monkeypatch):
    """屏蔽开发 .env 泄漏进 os.environ 的真实搜索配置（factory 等 import 时 load_dotenv），
    否则 api_key/provider 传 None/"" 的用例会回落到真 key 发起真实网络请求。"""
    for var in ("WEB_SEARCH_ENABLED", "WEB_SEARCH_PROVIDER", "WEB_SEARCH_API_KEY"):
        monkeypatch.delenv(var, raising=False)


class FakeAsyncClient:
    def __init__(self, response=None, exc: Exception | None = None):
        self.response = response
        self.exc = exc
        self.requests = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def post(self, url, **kwargs):
        self.requests.append({"url": url, **kwargs})
        if self.exc is not None:
            raise self.exc
        return self.response


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self.payload = payload
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                "request failed",
                request=httpx.Request("POST", "https://example.test"),
                response=httpx.Response(self.status_code),
            )

    def json(self):
        return self.payload


@pytest.mark.asyncio
async def test_search_returns_empty_when_web_search_disabled():
    client = WebSearchClient(enabled=False, provider="tavily", api_key="key")

    results = await client.search("latest agentic rag")

    assert results == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("provider", "api_key"),
    [
        (None, "key"),
        ("", "key"),
        ("tavily", None),
        ("tavily", ""),
        ("unknown", "key"),
    ],
)
async def test_search_returns_empty_when_provider_or_key_missing(provider, api_key):
    client = WebSearchClient(enabled=True, provider=provider, api_key=api_key)

    results = await client.search("latest agentic rag")

    assert results == []


@pytest.mark.asyncio
async def test_search_converts_tavily_results_to_web_evidence():
    fake_client = FakeAsyncClient(
        FakeResponse(
            {
                "results": [
                    {
                        "title": "Agentic RAG Guide",
                        "url": "https://example.com/agentic-rag",
                        "content": "Agentic RAG uses planning and fallback search.",
                    },
                    {
                        "title": "Snippet Result",
                        "url": "https://example.com/snippet",
                        "snippet": "Snippet text is accepted when content is absent.",
                    },
                ]
            }
        )
    )
    client = WebSearchClient(
        enabled=True,
        provider="tavily",
        api_key="tavily-key",
        http_client_factory=lambda: fake_client,
    )

    results = await client.search("latest agentic rag", max_results=2)

    assert len(results) == 2
    assert results[0].source == "web"
    assert results[0].title == "Agentic RAG Guide"
    assert results[0].url == "https://example.com/agentic-rag"
    assert results[0].content == "Agentic RAG uses planning and fallback search."
    assert results[0].metadata == {"provider": "tavily"}
    assert results[0].id == "web-tavily-b6a3385e8f3bdf3e"
    assert results[1].content == "Snippet text is accepted when content is absent."
    assert fake_client.requests == [
        {
            "url": "https://api.tavily.com/search",
            "json": {
                "api_key": "tavily-key",
                "query": "latest agentic rag",
                "max_results": 2,
            },
        }
    ]


@pytest.mark.asyncio
async def test_search_converts_serper_results_to_web_evidence():
    fake_client = FakeAsyncClient(
        FakeResponse(
            {
                "organic": [
                    {
                        "title": "Serper Result",
                        "link": "https://example.com/serper",
                        "snippet": "Serper organic snippets become evidence content.",
                    }
                ]
            }
        )
    )
    client = WebSearchClient(
        enabled=True,
        provider="serper",
        api_key="serper-key",
        http_client_factory=lambda: fake_client,
    )

    results = await client.search("latest agentic rag", max_results=3)

    assert len(results) == 1
    assert results[0].source == "web"
    assert results[0].title == "Serper Result"
    assert results[0].url == "https://example.com/serper"
    assert results[0].content == "Serper organic snippets become evidence content."
    assert results[0].metadata == {"provider": "serper"}
    assert results[0].id == "web-serper-26b11b1864d8f749"
    assert fake_client.requests == [
        {
            "url": "https://google.serper.dev/search",
            "headers": {"X-API-KEY": "serper-key"},
            "json": {"q": "latest agentic rag", "num": 3},
        }
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fake_client",
    [
        FakeAsyncClient(exc=httpx.ConnectError("offline")),
        FakeAsyncClient(FakeResponse({"results": []}, status_code=500)),
        FakeAsyncClient(FakeResponse({"results": "not-a-list"})),
    ],
)
async def test_search_returns_empty_on_request_or_parse_failures(fake_client):
    client = WebSearchClient(
        enabled=True,
        provider="tavily",
        api_key="key",
        http_client_factory=lambda: fake_client,
    )

    results = await client.search("latest agentic rag")

    assert results == []
