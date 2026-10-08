"""Playwright fallback for sites whose useful content is client-rendered."""

from urllib.parse import urlsplit

from playwright.async_api import async_playwright

from app.services.fetcher import FetchError, Fetcher
from app.services.urlsafe import UnsafeURLError, registered_domain, validate_public_url


async def render_public_page(url: str, fetcher: Fetcher, *, timeout_ms: int = 25_000) -> tuple[str, str]:
    """Render a public page without bypassing access controls or internal hosts."""
    normalized = await validate_public_url(url)
    if not await fetcher.robots.allowed(normalized):
        raise FetchError(f"robots.txt disallows browser fetch: {normalized}")
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        try:
            context = await browser.new_context(locale="en-US", user_agent=fetcher.user_agent)
            page = await context.new_page()

            async def guard(route):
                try:
                    requested = route.request.url
                    if requested.startswith(("data:", "blob:")):
                        await route.continue_()
                    else:
                        await validate_public_url(requested)
                        await route.continue_()
                except (UnsafeURLError, ValueError):
                    await route.abort()

            await page.route("**/*", guard)
            try:
                await page.goto(normalized, wait_until="networkidle", timeout=timeout_ms)
            except Exception:
                await page.goto(normalized, wait_until="load", timeout=timeout_ms)
            final_url = page.url
            if registered_domain(urlsplit(final_url).hostname or "") != registered_domain(urlsplit(normalized).hostname or ""):
                raise FetchError("Browser navigation redirected to an unrelated registered domain")
            text = await page.locator("body").inner_text(timeout=5000)
            html = await page.content()
            return (text[:200_000], html[:5_000_000])
        finally:
            await browser.close()
