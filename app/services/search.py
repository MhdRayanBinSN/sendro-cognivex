"""Replaceable async search provider interface and launch-source adapters."""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Protocol

import httpx

from app.config import get_settings


@dataclass(frozen=True)
class SearchResult:
    title: str
    url: str
    snippet: str
    source: str
    published_date: str | None = None


class SearchProvider(Protocol):
    async def search(self, query: str, days: int = 7) -> list[SearchResult]: ...


class TavilySearchProvider:
    def __init__(self, api_key: str, base_url: str | None = None) -> None:
        self.api_key = api_key
        self.base_url = base_url or get_settings().search_api_base_url

    async def search(self, query: str, days: int = 7) -> list[SearchResult]:
        today = datetime.now(timezone.utc).date()
        start_date = today - timedelta(days=days)
        async with httpx.AsyncClient(timeout=20, trust_env=False) as client:
            response = await client.post(self.base_url, json={"api_key": self.api_key, "query": query,
                "topic": "general", "time_range": None, "start_date": start_date.isoformat(),
                "end_date": today.isoformat(), "include_published_date": True,
                "search_depth": "basic", "max_results": 8})
            response.raise_for_status()
            payload = response.json()
        return [SearchResult(item.get("title", ""), item["url"], item.get("content", ""), "tavily",
                             item.get("published_date"))
                for item in payload.get("results", []) if item.get("url")]


class HackerNewsProvider:
    """Search Show HN posts through the public Algolia HN search API."""

    def __init__(self, base_url: str = "https://hn.algolia.com/api/v1/search_by_date") -> None:
        self.base_url = base_url

    async def search(self, query: str, days: int = 7) -> list[SearchResult]:
        since = int((datetime.now(timezone.utc) - timedelta(days=days)).timestamp())
        async with httpx.AsyncClient(timeout=15, trust_env=False) as client:
            response = await client.get(self.base_url, params={"query": query, "tags": "show_hn",
                "numericFilters": f"created_at_i>{since}", "hitsPerPage": 20})
            response.raise_for_status()
            payload = response.json()
        results: list[SearchResult] = []
        for item in payload.get("hits", []):
            url = item.get("url")
            if not url:
                continue
            snippet = item.get("story_text") or item.get("comment_text") or ""
            published_date = item.get("created_at")
            results.append(SearchResult(item.get("title", ""), url, snippet, "hacker_news", published_date))
        return results


def configured_search_provider() -> SearchProvider:
    settings = get_settings()
    if settings.search_provider == "tavily":
        key = settings.search_api_key.get_secret_value() if settings.search_api_key else ""
        if not key:
            raise RuntimeError("SEARCH_API_KEY is required when SEARCH_PROVIDER=tavily")
        return TavilySearchProvider(key, settings.search_api_base_url)
    if settings.search_provider in {"hacker_news", "hn"}:
        return HackerNewsProvider()
    raise ValueError(f"Unknown search provider: {settings.search_provider}")
