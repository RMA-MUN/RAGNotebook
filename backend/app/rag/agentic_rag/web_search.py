import hashlib
from collections.abc import Callable
from typing import Any

import httpx

from app.core.settings import settings
from app.rag.agentic_rag.schemas import Evidence


HttpClientFactory = Callable[[], httpx.AsyncClient]


class WebSearchClient:
    def __init__(
        self,
        enabled: bool | None = None,
        provider: str | None = None,
        api_key: str | None = None,
        http_client_factory: HttpClientFactory | None = None,
    ):
        self.enabled = _env_enabled() if enabled is None else enabled
        self.provider = (provider or settings.WEB_SEARCH_PROVIDER).strip().lower()
        self.api_key = (api_key or settings.WEB_SEARCH_API_KEY).strip()
        self.http_client_factory = http_client_factory or httpx.AsyncClient

    async def search(self, query: str, max_results: int = 5) -> list[Evidence]:
        if not self.enabled or not self.provider or not self.api_key:
            return []

        if self.provider == "tavily":
            return await self._search_tavily(query, max_results)
        if self.provider == "serper":
            return await self._search_serper(query, max_results)
        return []

    async def _search_tavily(self, query: str, max_results: int) -> list[Evidence]:
        try:
            async with self.http_client_factory() as client:
                response = await client.post(
                    "https://api.tavily.com/search",
                    json={
                        "api_key": self.api_key,
                        "query": query,
                        "max_results": max_results,
                    },
                )
            response.raise_for_status()
            payload = response.json()
            results = payload.get("results")
            if not isinstance(results, list):
                return []
            return [_to_evidence(self.provider, item) for item in results if isinstance(item, dict)]
        except Exception:
            return []

    async def _search_serper(self, query: str, max_results: int) -> list[Evidence]:
        try:
            async with self.http_client_factory() as client:
                response = await client.post(
                    "https://google.serper.dev/search",
                    headers={"X-API-KEY": self.api_key},
                    json={"q": query, "num": max_results},
                )
            response.raise_for_status()
            payload = response.json()
            results = payload.get("organic")
            if not isinstance(results, list):
                return []
            return [_to_evidence(self.provider, item) for item in results if isinstance(item, dict)]
        except Exception:
            return []


def _env_enabled() -> bool:
    return settings.WEB_SEARCH_ENABLED


def _to_evidence(provider: str, item: dict[str, Any]) -> Evidence:
    title = str(item.get("title") or "Web result")
    url = str(item.get("url") or item.get("link") or "")
    content = str(item.get("content") or item.get("snippet") or "")

    return Evidence(
        id=_evidence_id(provider, url, title),
        source="web",
        title=title,
        url=url,
        content=content,
        metadata={"provider": provider},
    )


def _evidence_id(provider: str, url: str, title: str) -> str:
    digest = hashlib.sha256(f"{provider}:{url}:{title}".encode("utf-8")).hexdigest()[:16]
    return f"web-{provider}-{digest}"
