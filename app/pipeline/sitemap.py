"""Robots-declared sitemap discovery, parsing and bounded recursion."""

import gzip
import io
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

from app.config import get_settings
from app.services.fetcher import FetchError, Fetcher
from app.services.browser import render_public_page
from app.services.urlsafe import UnsafeURLError, canonicalize_url
from app.services.urlsafe import registered_domain

SITEMAP_NS = "{http://www.sitemaps.org/schemas/sitemap/0.9}"


@dataclass(frozen=True)
class SitemapURL:
    url: str
    lastmod: str | None = None


@dataclass(frozen=True)
class SitemapDocument:
    urls: tuple[SitemapURL, ...]
    child_sitemaps: tuple[str, ...]


def parse_sitemap(data: bytes) -> SitemapDocument:
    """Parse sitemap URL sets or indexes, including gzip-compressed content."""
    if data[:2] == b"\x1f\x8b":
        with gzip.GzipFile(fileobj=io.BytesIO(data)) as compressed:
            data = compressed.read(20_000_001)
        if len(data) > 20_000_000:
            raise ValueError("Decompressed sitemap exceeds the 20 MB parsing limit")
    root = ET.fromstring(data)
    tag = root.tag.rsplit("}", 1)[-1].lower()
    if tag == "sitemapindex":
        children = tuple(node.text.strip() for node in root.findall(f"{SITEMAP_NS}sitemap/{SITEMAP_NS}loc")
                         if node.text and node.text.strip())
        return SitemapDocument((), children)
    if tag != "urlset":
        raise ValueError(f"Unsupported sitemap root element: {tag}")
    urls: list[SitemapURL] = []
    for node in root.findall(f"{SITEMAP_NS}url"):
        loc = node.findtext(f"{SITEMAP_NS}loc")
        if not loc:
            continue
        urls.append(SitemapURL(loc.strip(), node.findtext(f"{SITEMAP_NS}lastmod")))
    return SitemapDocument(tuple(urls), ())


def _same_site(url: str, origin_host: str) -> bool:
    host = (urlsplit(url).hostname or "").lower().rstrip(".")
    origin_host = origin_host.lower().rstrip(".")
    return bool(host and origin_host and registered_domain(host) == registered_domain(origin_host))


class SitemapMapper:
    def __init__(self, fetcher: Fetcher, *, max_files: int = 100, max_depth: int = 3) -> None:
        self.fetcher = fetcher
        self.max_files = max_files
        self.max_depth = max_depth

    async def discover(self, homepage_url: str) -> list[SitemapURL]:
        """Read sitemap declarations from robots.txt and recursively gather URLs."""
        homepage = await self.fetcher.fetch(homepage_url)
        origin_host = urlsplit(homepage.final_url).hostname or ""
        origin = urlsplit(homepage.final_url)
        robots_url = f"{origin.scheme}://{origin.netloc}/robots.txt"
        robots_result = await self.fetcher.fetch(robots_url, check_robots=False)
        sitemap_urls = [
            line.split(":", 1)[1].strip()
            for line in robots_result.body.decode("utf-8", errors="replace").splitlines()
            if line.lower().startswith("sitemap:") and ":" in line
        ]
        if not sitemap_urls:
            paths = [path.strip() for path in get_settings().sitemap_fallback_paths.split(",") if path.strip()]
            sitemap_urls = [urljoin(f"{origin.scheme}://{origin.netloc}/", path.lstrip("/")) for path in paths]

        pending = [(url, 0) for url in sitemap_urls]
        seen_files: set[str] = set()
        seen_pages: dict[str, SitemapURL] = {}
        while pending and len(seen_files) < self.max_files:
            sitemap_url, depth = pending.pop(0)
            if sitemap_url in seen_files or depth > self.max_depth or not _same_site(sitemap_url, origin_host):
                continue
            seen_files.add(sitemap_url)
            try:
                result = await self.fetcher.fetch(sitemap_url)
                document = parse_sitemap(result.body)
            except (FetchError, ValueError, ET.ParseError, OSError):
                continue
            for page in document.urls:
                if _same_site(page.url, origin_host):
                    seen_pages.setdefault(page.url, page)
            pending.extend((child, depth + 1) for child in document.child_sitemaps
                           if child not in seen_files and _same_site(child, origin_host))
        if not seen_pages:
            try:
                _, rendered_html = await render_public_page(homepage.final_url, self.fetcher)
                link_source = rendered_html.encode("utf-8")
            except Exception:
                link_source = homepage.body
            links = _extract_links(link_source, homepage.final_url)
            for link in links:
                if _same_site(link.url, origin_host):
                    seen_pages.setdefault(link.url, link)
        return list(seen_pages.values())


class _AnchorParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[tuple[str, str]] = []
        self.href: str | None = None
        self.text: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag.lower() == "a":
            self.href = dict(attrs).get("href")
            self.text = []

    def handle_data(self, data):
        if self.href is not None:
            self.text.append(data)

    def handle_endtag(self, tag):
        if tag.lower() == "a" and self.href:
            self.links.append((self.href, " ".join(self.text).strip()))
            self.href = None
            self.text = []


def _extract_links(body: bytes, base_url: str) -> list[SitemapURL]:
    parser = _AnchorParser()
    parser.feed(body.decode("utf-8", errors="replace"))
    found: dict[str, SitemapURL] = {}
    for href, _ in parser.links:
        try:
            url = canonicalize_url(urljoin(base_url, href))
        except UnsafeURLError:
            continue
        if urlsplit(url).path.lower().endswith((".pdf", ".jpg", ".png", ".zip")):
            continue
        found.setdefault(url, SitemapURL(url))
    return list(found.values())
