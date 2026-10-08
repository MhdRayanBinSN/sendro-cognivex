"""Deterministic candidate liveness and content quality checks."""

import re
from urllib.parse import urlsplit
from dataclasses import dataclass
from html.parser import HTMLParser

from app.pipeline.schemas import Candidate
from app.services.browser import render_public_page
from app.services.fetcher import Fetcher


class _VisibleText(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style", "noscript", "svg"}:
            self.hidden += 1

    def handle_endtag(self, tag):
        if tag in {"script", "style", "noscript", "svg"} and self.hidden:
            self.hidden -= 1

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


@dataclass(frozen=True)
class ValidationResult:
    candidate: Candidate
    valid: bool
    reason: str
    final_url: str = ""
    word_count: int = 0


async def validate_candidate(candidate: Candidate, fetcher: Fetcher) -> ValidationResult:
    rendered_text = ""
    final_url = str(candidate.url)
    try:
        response = await fetcher.fetch(str(candidate.url))
    except Exception as exc:
        try:
            rendered_text, _ = await render_public_page(str(candidate.url), fetcher)
        except Exception:
            return ValidationResult(candidate, False, f"fetch failed: {type(exc).__name__}")
        response = None
    if response is not None:
        final_url = response.final_url
        if response.status_code != 200:
            if response.status_code not in (401, 403, 429):
                return ValidationResult(candidate, False, f"HTTP {response.status_code}", final_url)
            try:
                rendered_text, _ = await render_public_page(final_url, fetcher)
            except Exception:
                return ValidationResult(candidate, False, f"HTTP {response.status_code}", final_url)
        else:
            content_type = response.content_type.lower()
            if "html" not in content_type and "xhtml" not in content_type:
                return ValidationResult(candidate, False, "not an HTML product site", final_url)
            parser = _VisibleText()
            try:
                parser.feed(response.body.decode("utf-8", errors="replace"))
            except Exception:
                return ValidationResult(candidate, False, "HTML parsing failed", final_url)
            text = re.sub(r"\s+", " ", " ".join(parser.parts)).strip()
    else:
        text = rendered_text
    final_parts = urlsplit(final_url)
    host_parts = (final_parts.hostname or "").lower().split(".")
    content_subdomains = {"blog", "news", "press", "docs", "documentation", "help", "support", "learn",
                          "review", "reviews", "directory", "compare", "comparison"}
    content_paths = {"blog", "news", "press", "docs", "documentation", "help", "support",
                     "articles", "article", "posts", "updates", "stories", "learn"}
    registered_host = (final_parts.hostname or "").lower()
    directory_domain = any(signal in registered_host.split(".", 1)[0]
                           for signal in ("review", "directory", "alternatives", "compare"))
    if (content_subdomains.intersection(host_parts) or directory_domain
            or final_parts.path.strip("/").split("/", 1)[0].casefold() in content_paths):
        return ValidationResult(candidate, False, "article or documentation URL, not a product site", final_url)
    count = len(text.split())
    if count < 300:
        try:
            browser_text, _ = await render_public_page(final_url, fetcher)
            if len(browser_text.split()) > count:
                text = re.sub(r"\s+", " ", browser_text).strip()
            count = len(text.split())
        except Exception:
            pass
    lowered = text.lower()
    if count < 300:
        return ValidationResult(candidate, False, "insufficient page text", final_url, count)
    if any(phrase in lowered for phrase in ("verify you are human", "checking your browser", "captcha", "access denied")):
        return ValidationResult(candidate, False, "bot challenge or access restriction", final_url, count)
    if any(phrase in lowered for phrase in ("domain for sale", "buy this domain", "parked free")):
        return ValidationResult(candidate, False, "parked domain", final_url, count)
    if any(phrase in lowered for phrase in ("coming soon", "join the waitlist", "request early access")) and count < 700:
        return ValidationResult(candidate, False, "waitlist-only or coming soon", final_url, count)
    return ValidationResult(candidate, True, "validated", final_url, count)
