"""Robots policy lookup and conservative per-host request pacing."""

import asyncio
import time
from urllib.parse import urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

import httpx


class DomainRateLimiter:
    """Ensure sequential requests to a host are separated by a minimum delay."""

    def __init__(self, requests_per_second: float = 1.0) -> None:
        if requests_per_second <= 0:
            raise ValueError("requests_per_second must be positive")
        self._interval = 1.0 / requests_per_second
        self._locks: dict[str, asyncio.Lock] = {}
        self._last_request: dict[str, float] = {}

    async def wait(self, url: str) -> None:
        host = urlsplit(url).netloc.lower()
        lock = self._locks.setdefault(host, asyncio.Lock())
        async with lock:
            delay = self._interval - (time.monotonic() - self._last_request.get(host, 0))
            if delay > 0:
                await asyncio.sleep(delay)
            self._last_request[host] = time.monotonic()


class RobotsChecker:
    """Fetch and cache robots.txt policy; failures deny crawling by default."""

    def __init__(self, user_agent: str, client: httpx.AsyncClient, limiter: DomainRateLimiter | None = None,
                 ttl_seconds: int = 3600) -> None:
        self.user_agent = user_agent
        self.client = client
        self.limiter = limiter
        self.ttl_seconds = ttl_seconds
        self._cache: dict[str, tuple[float, RobotFileParser | None]] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    async def _get_parser(self, url: str) -> RobotFileParser | None:
        parts = urlsplit(url)
        origin = urlunsplit((parts.scheme, parts.netloc, "/robots.txt", "", ""))
        cached = self._cache.get(origin)
        if cached and time.monotonic() - cached[0] < self.ttl_seconds:
            return cached[1]
        lock = self._locks.setdefault(origin, asyncio.Lock())
        async with lock:
            cached = self._cache.get(origin)
            if cached and time.monotonic() - cached[0] < self.ttl_seconds:
                return cached[1]
            parser = RobotFileParser(origin)
            try:
                if self.limiter is not None:
                    await self.limiter.wait(origin)
                response = await self.client.get(origin, timeout=10)
                if response.status_code in (401, 403):
                    parser.parse(["User-agent: *", "Disallow: /"])
                elif response.status_code == 404:
                    parser.parse(["User-agent: *", "Allow: /"])
                elif response.is_success:
                    parser.parse(response.text.splitlines())
                else:
                    parser = None
            except httpx.HTTPError:
                parser = None
            self._cache[origin] = (time.monotonic(), parser)
            return parser

    async def allowed(self, url: str) -> bool:
        parser = await self._get_parser(url)
        return parser is not None and parser.can_fetch(self.user_agent, url)
