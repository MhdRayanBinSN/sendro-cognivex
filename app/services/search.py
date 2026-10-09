"""Replaceable async search provider interface and launch-source adapters."""

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol

import httpx

from app.config import get_settings


@dataclass(frozen=True)
class SearchResult:
    title: str
    url: str
    snippet: str
    source: str
    published_date: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


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


class GitHubSearchProvider:
    """Search public repositories and expose compact, attributable repo signals."""

    def __init__(self, token: str | None = None) -> None:
        self.token = token
        self.base_url = "https://api.github.com/search/repositories"

    async def search(self, query: str, days: int = 365) -> list[SearchResult]:
        del days  # Repository metadata is current-state evidence, not launch-date evidence.
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": get_settings().user_agent,
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        async with httpx.AsyncClient(timeout=15, trust_env=False) as client:
            response = await client.get(self.base_url, params={
                "q": f"{query} in:name,description,readme",
                "sort": "stars", "order": "desc", "per_page": 10,
            }, headers=headers)
            response.raise_for_status()
            payload = response.json()
        results = []
        for item in payload.get("items", []):
            repo_url = item.get("html_url")
            if not repo_url:
                continue
            license_info = item.get("license") or {}
            topics = item.get("topics") or []
            metadata = {
                "full_name": item.get("full_name", ""),
                "stars": int(item.get("stargazers_count", 0) or 0),
                "forks": int(item.get("forks_count", 0) or 0),
                "language": item.get("language"),
                "license": license_info.get("spdx_id") or license_info.get("name"),
                "topics": topics[:10],
                "homepage": item.get("homepage") or None,
                "created_at": item.get("created_at"),
                "updated_at": item.get("updated_at"),
                "archived": bool(item.get("archived", False)),
            }
            description = (item.get("description") or "No repository description").strip()
            summary = (f"{description} GitHub repository {metadata['full_name']}; "
                       f"stars: {metadata['stars']}; forks: {metadata['forks']}; "
                       f"language: {metadata['language'] or 'not specified'}; "
                       f"license: {metadata['license'] or 'not specified'}; "
                       f"topics: {', '.join(topics[:8]) or 'none'}; "
                       f"homepage: {metadata['homepage'] or 'not specified'}")
            results.append(SearchResult(
                title=f"{metadata['full_name']} · GitHub repository",
                url=repo_url, snippet=summary[:1200], source="github",
                published_date=metadata["updated_at"], metadata=metadata,
            ))
        return results


class ProductHuntAlgoliaProvider:
    """Search Product Hunt's public Algolia index using its search-only key."""

    def __init__(self, app_id: str, search_key: str, index: str = "Post_production") -> None:
        self.app_id = app_id.strip()
        self.search_key = search_key
        self.index = index.strip() or "Post_production"
        self.base_url = f"https://{self.app_id.lower()}-dsn.algolia.net/1/indexes/{self.index}"

    async def search(self, query: str, days: int = 365) -> list[SearchResult]:
        del days  # Apply the date window after reading Product Hunt's post date.
        headers = {
            "X-Algolia-API-Key": self.search_key,
            "X-Algolia-Application-Id": self.app_id,
            "User-Agent": get_settings().user_agent,
        }
        async with httpx.AsyncClient(timeout=15, trust_env=False) as client:
            response = await client.get(self.base_url, params={"query": query, "hitsPerPage": 50},
                                        headers=headers)
            response.raise_for_status()
            payload = response.json()
        results: list[SearchResult] = []
        for hit in payload.get("hits", []):
            name = _first_value(hit, "name", "title", "product_name", "productName")
            if not name:
                continue
            website = _first_url(hit, "website", "website_url", "websiteUrl", "product_url",
                                 "productUrl", "redirect_url", "target_url", "homepage")
            if not website:
                product = hit.get("product")
                if isinstance(product, dict):
                    website = _first_url(product, "website", "website_url", "websiteUrl", "url")
            post_url = _first_url(hit, "post_url", "postUrl", "url", "permalink")
            slug = _first_value(hit, "slug", "post_slug", "postSlug")
            if not post_url and slug:
                post_url = f"https://www.producthunt.com/posts/{slug.strip('/')}"
            if not post_url:
                object_id = _first_value(hit, "objectID", "id")
                if object_id:
                    post_url = f"https://www.producthunt.com/posts/{object_id}"
            if not post_url:
                continue
            published_date = _first_value(hit, "created_at", "createdAt", "published_at", "publishedAt")
            if not published_date:
                published_date = _epoch_date(hit.get("created_at_i") or hit.get("createdAtI"))
            tagline = _first_value(hit, "tagline", "subtitle", "product_tagline", "productTagline")
            description = _first_value(hit, "description", "overview", "text")
            topics = hit.get("topics") or hit.get("topics_names") or hit.get("topicsNames") or []
            if isinstance(topics, dict):
                topics = [item.get("name", "") for item in topics.get("data", []) if isinstance(item, dict)]
            if not isinstance(topics, list):
                topics = [str(topics)]
            metadata = {
                "name": name,
                "tagline": tagline,
                "description": description[:1200],
                "homepage": website,
                "post_url": post_url,
                "created_at": published_date,
                "votes_count": hit.get("votesCount", hit.get("votes_count", hit.get("votes"))),
                "topics": [str(item) for item in topics[:10]],
            }
            snippet = ". ".join(part for part in (
                f"Product Hunt post created {published_date}" if published_date else "Product Hunt post",
                tagline,
                description,
                f"Official product website: {website}" if website else "",
                f"Topics: {', '.join(metadata['topics'])}" if metadata["topics"] else "",
            ) if part)
            results.append(SearchResult(title=name, url=post_url, snippet=snippet[:1800],
                                        source="product_hunt_algolia", published_date=published_date,
                                        metadata=metadata))
        return results


def _first_value(payload: dict, *keys: str) -> str:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _first_url(payload: dict, *keys: str) -> str:
    value = _first_value(payload, *keys)
    if value:
        return value
    for key in keys:
        nested = payload.get(key)
        if isinstance(nested, dict):
            url = _first_value(nested, "url", "href")
            if url:
                return url
    return ""


def _epoch_date(value) -> str | None:
    if isinstance(value, (int, float)):
        timestamp = value / 1000 if value > 10_000_000_000 else value
        try:
            return datetime.fromtimestamp(timestamp, timezone.utc).isoformat()
        except (OverflowError, OSError, ValueError):
            return None
    return None


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
