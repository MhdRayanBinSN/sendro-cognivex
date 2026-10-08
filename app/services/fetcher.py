"""Bounded asynchronous HTTP fetcher with URL and robots checks."""

import asyncio
import random
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx

from app.config import get_settings
from app.services.robots import DomainRateLimiter, RobotsChecker
from app.services.urlsafe import registered_domain, validate_public_url


@dataclass(frozen=True)
class FetchResult:
    url: str
    final_url: str
    status_code: int
    content_type: str
    body: bytes


class FetchError(RuntimeError):
    """Raised when fetching fails or policy disallows a URL."""


class Fetcher:
    def __init__(self, user_agent: str | None = None, max_bytes: int = 5_000_000,
                 max_redirects: int = 5, requests_per_second: float = 1.0) -> None:
        self.user_agent = user_agent or get_settings().user_agent
        self.max_bytes = max_bytes
        self.max_redirects = max_redirects
        self.client = httpx.AsyncClient(headers={"User-Agent": self.user_agent}, follow_redirects=False,
                                        timeout=httpx.Timeout(20.0, connect=8.0), trust_env=False)
        self.limiter = DomainRateLimiter(requests_per_second)
        self.robots = RobotsChecker(self.user_agent, self.client, self.limiter)

    async def close(self) -> None:
        await self.client.aclose()

    async def fetch(self, url: str, *, check_robots: bool = True, retries: int = 2) -> FetchResult:
        current = await validate_public_url(url)
        if check_robots and not await self.robots.allowed(current):
            raise FetchError(f"robots.txt disallows fetching {current}")
        for attempt in range(retries + 1):
            try:
                return await self._fetch_redirects(current)
            except httpx.HTTPStatusError as exc:
                transient = exc.response.status_code == 429 or 500 <= exc.response.status_code < 600
                if not transient or attempt >= retries:
                    raise FetchError(f"Transient HTTP status {exc.response.status_code}: {current}") from exc
                await asyncio.sleep(min(4.0, 0.25 * 2**attempt) + random.random() * 0.15)
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                if attempt >= retries:
                    raise FetchError(f"HTTP fetch failed after retries: {current}") from exc
                await asyncio.sleep(min(4.0, 0.25 * 2**attempt) + random.random() * 0.15)
        raise AssertionError("unreachable")

    async def _fetch_redirects(self, url: str) -> FetchResult:
        current = url
        initial_host = urlsplit(url).hostname or ""
        initial_domain = registered_domain(initial_host)
        for hop in range(self.max_redirects + 1):
            current = await validate_public_url(current)
            await self.limiter.wait(current)
            async with self.client.stream("GET", current) as response:
                if response.status_code == 429 or 500 <= response.status_code < 600:
                    raise httpx.HTTPStatusError("Transient HTTP status", request=response.request, response=response)
                if response.is_redirect:
                    if hop == self.max_redirects:
                        raise FetchError(f"Too many redirects from {url}")
                    location = response.headers.get("location")
                    if not location:
                        raise FetchError(f"Redirect without Location header: {current}")
                    current = str(response.url.join(location))
                    next_host = urlsplit(current).hostname or ""
                    if registered_domain(next_host) != initial_domain:
                        raise FetchError(f"Redirect moved to an unrelated registered domain: {current}")
                    continue
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    body.extend(chunk)
                    if len(body) > self.max_bytes:
                        raise FetchError(f"Response exceeded {self.max_bytes} bytes: {current}")
                return FetchResult(url=url, final_url=str(response.url), status_code=response.status_code,
                                   content_type=response.headers.get("content-type", ""), body=bytes(body))
        raise AssertionError("unreachable")
