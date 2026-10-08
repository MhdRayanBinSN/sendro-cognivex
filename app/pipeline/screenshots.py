"""Policy-aware browser screenshots with visual quality and vision checks."""

import asyncio
import base64
import hashlib
import json
import statistics
import httpx
from pathlib import Path
from urllib.parse import urlsplit

from PIL import Image, ImageStat
from pydantic import BaseModel
from playwright.async_api import async_playwright

from app.config import get_settings
from app.db import LLMCall, engine
from app.orchestrator import RunBudgetExceeded
from app.services.fetcher import Fetcher
from app.services.urlsafe import UnsafeURLError, validate_public_url
from sqlmodel import Session, select


class ScreenshotJudgment(BaseModel):
    usable: bool
    issue_type: str | None = None
    caption: str


class ScreenshotResult(BaseModel):
    url: str
    path: str
    width: int
    height: int
    accepted: bool
    caption: str
    issue: str | None = None


class ScreenshotCaptureError(RuntimeError):
    def __init__(self, message: str, results: list[ScreenshotResult]):
        super().__init__(message)
        self.results = results


def _image_variance(path: Path) -> float:
    with Image.open(path) as image:
        stats = ImageStat.Stat(image.convert("RGB"))
        return statistics.mean(stats.var)


async def _judge(path: Path, intended_page: str, run_id: int | None = None) -> ScreenshotJudgment:
    settings = get_settings()
    provider = settings.llm_provider.lower()
    image_bytes = path.read_bytes()
    if run_id is not None:
        with Session(engine) as session:
            calls = session.exec(select(LLMCall).where(LLMCall.run_id == run_id)).all()
            spent = sum(call.cost_usd for call in calls)
            if settings.llm_provider.lower() == "gemini":
                input_rate = settings.gemini_strong_input_usd_per_million
                output_rate = settings.gemini_strong_output_usd_per_million
            elif settings.llm_provider.lower() == "groq":
                input_rate = settings.groq_vision_input_usd_per_million
                output_rate = settings.groq_vision_output_usd_per_million
            else:
                input_rate = settings.llm_strong_input_usd_per_million
                output_rate = settings.llm_strong_output_usd_per_million
            estimate = (5000 * input_rate + 300 * output_rate) / 1_000_000
            if spent + estimate >= settings.max_run_cost_usd:
                raise RunBudgetExceeded("Run cost budget reached before screenshot review")
    encoded = base64.b64encode(image_bytes).decode("ascii")
    model = settings.llm_model_vision
    prompt = (f"Intended page: {intended_page}. Schema: {json.dumps(ScreenshotJudgment.model_json_schema())}. "
              "Identify blank pages, cookie/login walls, 404s and CAPTCHAs. Return JSON only.")
    if provider == "gemini":
        if settings.gemini_api_key is None:
            raise RuntimeError("GEMINI_API_KEY is required for screenshot quality review")
        from google import genai
        from google.genai import types
        client = genai.Client(api_key=settings.gemini_api_key.get_secret_value())
        response = await client.aio.models.generate_content(
            model=settings.llm_model_vision,
            contents=[prompt, types.Part.from_bytes(data=image_bytes, mime_type="image/png")],
            config={"max_output_tokens": 300, "temperature": 0,
                    "response_mime_type": "application/json",
                    "response_schema": ScreenshotJudgment},
        )
        text = response.text or "{}"
        usage = response.usage_metadata
        input_tokens = usage.prompt_token_count or 0
        output_tokens = usage.candidates_token_count or 0
        input_rate = settings.gemini_strong_input_usd_per_million
        output_rate = settings.gemini_strong_output_usd_per_million
    elif provider == "groq":
        if settings.groq_api_key is None or not settings.groq_api_key.get_secret_value():
            raise RuntimeError("GROQ_API_KEY is required for screenshot quality review")
        async with httpx.AsyncClient(timeout=90, trust_env=False) as client:
            response = await client.post(
                settings.groq_api_base_url,
                headers={"Authorization": f"Bearer {settings.groq_api_key.get_secret_value()}",
                         "Content-Type": "application/json"},
                json={
                    "model": model,
                    "messages": [
                        {"role": "system", "content": "Judge whether the screenshot is useful. Return JSON only."},
                        {"role": "user", "content": [
                            {"type": "text", "text": prompt},
                            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{encoded}"}},
                        ]},
                    ],
                    "max_completion_tokens": 300,
                    "response_format": {"type": "json_object"},
                },
            )
            response.raise_for_status()
            payload = response.json()
        text = payload.get("choices", [{}])[0].get("message", {}).get("content", "{}")
        usage = payload.get("usage", {})
        input_tokens = usage.get("prompt_tokens", 0)
        output_tokens = usage.get("completion_tokens", 0)
        input_rate = settings.groq_vision_input_usd_per_million
        output_rate = settings.groq_vision_output_usd_per_million
    else:
        if settings.anthropic_api_key is None:
            raise RuntimeError("ANTHROPIC_API_KEY is required for screenshot quality review")
        from anthropic import AsyncAnthropic
        client = AsyncAnthropic(api_key=settings.anthropic_api_key.get_secret_value())
        response = await client.messages.create(
            model=settings.llm_model_strong, max_tokens=300,
            system="Judge whether this screenshot is a useful view of the intended public product page. Return JSON only.",
            messages=[{"role": "user", "content": [
                {"type": "text", "text": prompt},
                {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": encoded}},
            ]}],
        )
        text = "".join(block.text for block in response.content if getattr(block, "type", None) == "text")
        usage = response.usage
        input_tokens = usage.input_tokens
        output_tokens = usage.output_tokens
        input_rate = settings.llm_strong_input_usd_per_million
        output_rate = settings.llm_strong_output_usd_per_million
    judgment = ScreenshotJudgment.model_validate_json(text)
    if run_id is not None:
        cost = (input_tokens * input_rate + output_tokens * output_rate) / 1_000_000
        with Session(engine) as session:
            session.add(LLMCall(run_id=run_id, stage="screenshot_judge",
                prompt_hash=hashlib.sha256(intended_page.encode() + image_bytes).hexdigest(),
                model=model, input_tokens=input_tokens,
                output_tokens=output_tokens, cost_usd=cost,
                response_json=judgment.model_dump(mode="json"),
                reasoning=f"usable={judgment.usable}; issue={judgment.issue_type or 'none'}"))
            session.commit()
    return judgment


async def capture_screenshots(urls: list[str], product_slug: str, fetcher: Fetcher,
                              *, minimum: int | None = None, run_id: int | None = None) -> list[ScreenshotResult]:
    """Capture selected URLs; reject inaccessible, blank, and vision-rejected images."""
    settings = get_settings()
    required = minimum if minimum is not None else settings.min_screenshots
    output_dir = Path(settings.screenshots_dir) / product_slug
    output_dir.mkdir(parents=True, exist_ok=True)
    results: list[ScreenshotResult] = []
    semaphore = asyncio.Semaphore(2)
    vision_semaphore = asyncio.Semaphore(1)
    vision_review_count = 0

    async def capture(index: int, url: str, browser) -> ScreenshotResult:
        nonlocal vision_review_count
        async with semaphore:
            normalized = await validate_public_url(url)
            if not await fetcher.robots.allowed(normalized):
                return ScreenshotResult(url=url, path="", width=0, height=0, accepted=False,
                                        caption="", issue="robots.txt disallows capture")
            context = None
            try:
                context = await browser.new_context(viewport={"width": 1440, "height": 900},
                                                    locale="en-US", color_scheme="light")
                page = await context.new_page()

                async def guard_route(route):
                    try:
                        request_url = route.request.url
                        if request_url.startswith(("data:", "blob:")):
                            await route.continue_()
                            return
                        await validate_public_url(request_url)
                        await route.continue_()
                    except (UnsafeURLError, ValueError):
                        await route.abort()

                await page.route("**/*", guard_route)
                issue = None
                for attempt in range(2):
                    issue = None
                    try:
                        try:
                            # Most product sites keep analytics and chat sockets open, so
                            # networkidle can waste 25 seconds without making the image better.
                            await page.goto(normalized, wait_until="domcontentloaded", timeout=15000)
                        except Exception:
                            await page.goto(normalized, wait_until="commit", timeout=10000)
                        try:
                            await page.wait_for_load_state("load", timeout=4000)
                        except Exception:
                            pass
                        for label in ("Accept", "Accept all", "Agree", "I agree", "Close"):
                            button = page.get_by_role("button", name=label, exact=False).first
                            if await button.count() and await button.is_visible():
                                try:
                                    await button.click(timeout=1000)
                                    break
                                except Exception:
                                    pass
                        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                        await page.wait_for_timeout(500)
                        max_scroll = await page.evaluate(
                            "Math.max(0, document.documentElement.scrollHeight - window.innerHeight)")
                        section_slot = index % 3
                        scroll_y = round(max_scroll * (section_slot / 2)) if max_scroll else 0
                        await page.evaluate("y => window.scrollTo(0, y)", scroll_y)
                        await page.wait_for_timeout(800 if attempt == 0 else 1800)
                        page_title = (await page.title()).strip()
                        try:
                            body_text = await page.locator("body").inner_text(timeout=1500)
                        except Exception:
                            body_text = ""
                        visible_text = " ".join(body_text.split())
                        lowered_text = visible_text.casefold()
                        error_markers = ("verify you are human", "checking your browser", "captcha",
                                         "access denied", "page not found", "404 not found", "sign in to continue")
                        if any(marker in lowered_text for marker in error_markers):
                            issue = "page displayed a challenge, login wall, or error message"
                            continue
                        if len(visible_text) < 30 and not page_title:
                            issue = "page has no visible title or content"
                            continue
                        path = output_dir / f"page-{index + 1}.png"
                        await page.screenshot(path=str(path), animations="disabled")
                        with Image.open(path) as image:
                            width, height = image.size
                        if _image_variance(path) < 8:
                            issue = "near-uniform image"
                            continue
                        async with vision_semaphore:
                            should_use_vision = vision_review_count < 1
                            if should_use_vision:
                                vision_review_count += 1
                        section = ("top section" if section_slot == 0 else
                                   "middle section" if section_slot == 1 else "lower section")
                        caption = (f"{page_title or urlsplit(normalized).path or 'Product page'} · {section}; "
                                   "visible content and image quality checks passed.")
                        if should_use_vision:
                            try:
                                judgment = await _judge(path, normalized, run_id)
                            except Exception as exc:
                                status = (getattr(exc, "status", None) or getattr(exc, "code", None)
                                          or getattr(getattr(exc, "response", None), "status_code", None))
                                if status != 429:
                                    raise
                                issue = "vision quota unavailable; deterministic page and image checks passed"
                            else:
                                if not judgment.usable:
                                    return ScreenshotResult(url=normalized, path="", width=0, height=0,
                                        accepted=False, caption="",
                                        issue=judgment.issue_type or "vision quality check rejected screenshot")
                                caption = judgment.caption
                        if not issue or issue.startswith("vision quota unavailable"):
                            return ScreenshotResult(url=normalized, path=str(path), width=width, height=height,
                                                    accepted=True, caption=caption, issue=issue)
                    except Exception as exc:
                        issue = type(exc).__name__
                return ScreenshotResult(url=normalized, path="", width=0, height=0, accepted=False,
                                        caption="", issue=issue)
            finally:
                if context is not None:
                    await context.close()

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        try:
            results = await asyncio.gather(*(capture(i, url, browser) for i, url in enumerate(urls)))
        finally:
            await browser.close()
    accepted = [item for item in results if item.accepted]
    if len(accepted) < required:
        raise ScreenshotCaptureError(f"Only {len(accepted)} screenshots passed; {required} required", results)
    return results
