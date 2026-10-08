"""End-to-end background run composition for the product research pipeline."""

import hashlib
import logging
import re
from datetime import timedelta
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit

import trafilatura
from sqlmodel import Session, select

from app.config import get_settings
from app.db import (Comparison, Fact, Page, Product, Run, Screenshot, StageLog,
                    engine, utc_now)
from app.orchestrator import execute_stages
from app.pipeline.compare import compare_products
from app.pipeline.discover import discover
from app.pipeline.extract import extract_facts
from app.pipeline.gapfill import choose_gap_pages
from app.pipeline.page_picker import pick_pages
from app.pipeline.render import render_html
from app.pipeline.schemas import Candidate, URLInfo
from app.pipeline.schemas import ComparisonData, ComparisonRow
from app.pipeline.screenshots import capture_screenshots
from app.pipeline.select import select_pair
from app.pipeline.sitemap import SitemapMapper
from app.pipeline.validate import validate_candidate
from app.services.fetcher import Fetcher
from app.services.browser import render_public_page
from app.services.search import configured_search_provider
from app.services.urlsafe import canonicalize_url, registered_domain

logger = logging.getLogger(__name__)


class _TextParser(HTMLParser):
    def __init__(self):
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


def _extract_text(html: bytes, url: str) -> str:
    raw = html.decode("utf-8", errors="replace")
    extracted = trafilatura.extract(raw, url=url, include_comments=False, include_tables=True)
    if extracted:
        return extracted[:200_000]
    parser = _TextParser()
    parser.feed(raw)
    return re.sub(r"\s+", " ", " ".join(parser.parts)).strip()[:200_000]


def _url_info(url: str, homepage: str) -> URLInfo:
    parts = urlsplit(url)
    home_host = urlsplit(homepage).hostname or ""
    page_host = parts.hostname or ""
    path = parts.path or "/"
    depth = len([segment for segment in path.split("/") if segment])
    first = next((segment for segment in path.split("/") if segment), "home")
    return URLInfo(url=canonicalize_url(url), path=path, depth=depth,
                   title=None, anchor_text=None,
                   group=first if registered_domain(page_host) == registered_domain(home_host) else page_host)


def _domain_for(url: str) -> str:
    host = urlsplit(url).hostname or ""
    return registered_domain(host)


def _evidence_benchmarks(first: dict, second: dict) -> ComparisonData:
    """Build honest coverage benchmarks when narrative comparison is unavailable."""
    dimensions = ("pricing", "core_features", "integrations", "target_users", "security_compliance")
    rows = []
    first_fields = {str(item.get("field", "")).casefold() for item in first.get("facts", [])}
    second_fields = {str(item.get("field", "")).casefold() for item in second.get("facts", [])}
    for dimension in dimensions:
        aliases = set(dimension.split("_"))
        a_matches = [item for item in first.get("facts", [])
                     if aliases.intersection(str(item.get("field", "")).casefold().replace("-", "_").split("_"))]
        b_matches = [item for item in second.get("facts", [])
                     if aliases.intersection(str(item.get("field", "")).casefold().replace("-", "_").split("_"))]
        a_value = a_matches[0].get("value", "Evidence not found") if a_matches else "Evidence not found"
        b_value = b_matches[0].get("value", "Evidence not found") if b_matches else "Evidence not found"
        rows.append(ComparisonRow(
            dimension=f"Evidence coverage: {dimension.replace('_', ' ')}",
            a_value=str(a_value), b_value=str(b_value),
            score_a=100 if a_matches else 0, score_b=100 if b_matches else 0,
            verdict="tie" if bool(a_matches) == bool(b_matches) else ("a" if a_matches else "b"),
            evidence_fact_ids=[int(item["id"]) for item in a_matches[:1] + b_matches[:1] if item.get("id")],
        ))
    return ComparisonData(reasoning="Deterministic evidence coverage fallback; no product quality score was inferred.",
                          rows=rows,
                          confidence_notes="Benchmark scores show whether evidence was found for each dimension, not product quality.")


def _upsert_product(session: Session, candidate: Candidate) -> Product:
    domain = _domain_for(str(candidate.url))
    product = session.exec(select(Product).where(Product.canonical_domain == domain)).first()
    if product is None:
        product = Product(name=candidate.name, canonical_domain=domain, homepage_url=str(candidate.url),
                           description=candidate.description)
        session.add(product)
    else:
        product.name = candidate.name
        product.homepage_url = str(candidate.url)
        product.description = candidate.description
    session.commit()
    session.refresh(product)
    return product


async def _research_product(candidate: Candidate, category: str, run_id: int, session: Session,
                            fetcher: Fetcher) -> dict:
    settings = get_settings()
    product = _upsert_product(session, candidate)
    urls = await SitemapMapper(fetcher).discover(str(candidate.url))
    homepage = canonicalize_url(str(candidate.url))
    url_values = [homepage] + [url for url in dict.fromkeys(item.url for item in urls) if url != homepage]
    all_infos = [_url_info(url, homepage) for url in url_values]
    compact_urls: list[URLInfo] = []
    grouped: dict[str, list[URLInfo]] = {}
    for info in all_infos:
        grouped.setdefault(info.group, []).append(info)
    compact_urls.extend(all_infos[:1])
    selected_urls = {str(info.url) for info in compact_urls}
    for group_items in grouped.values():
        for info in group_items[:3]:
            if len(compact_urls) >= settings.max_page_picker_urls:
                break
            if str(info.url) not in selected_urls:
                compact_urls.append(info)
                selected_urls.add(str(info.url))
        if len(compact_urls) >= settings.max_page_picker_urls:
            break
    for info in sorted(all_infos, key=lambda item: (item.depth, item.path)):
        if len(compact_urls) >= settings.max_page_picker_urls:
            break
        if str(info.url) not in selected_urls:
            compact_urls.append(info)
            selected_urls.add(str(info.url))
    plan = await pick_pages(category, candidate.name, compact_urls, run_id=run_id, session=session)
    facts_output: list[dict] = []
    page_errors: list[str] = []
    pages_by_url: dict[str, Page] = {}
    used_urls: set[str] = set()

    async def research_page(item) -> str | None:
        page_url = canonicalize_url(str(item.url))
        original_url = page_url
        used_urls.add(original_url)
        page = session.exec(select(Page).where(Page.product_id == product.id, Page.url == page_url)).first()
        if page and page.cleaned_text and page.fetched_at and utc_now() - page.fetched_at < timedelta(hours=24):
            text = page.cleaned_text
        else:
            browser_fallback = False
            try:
                result = await fetcher.fetch(page_url)
            except Exception:
                rendered_text, rendered_html = await render_public_page(page_url, fetcher)
                text = rendered_text
                browser_fallback = True
                result = None
            if result is not None:
                if result.status_code != 200 or "html" not in result.content_type.lower():
                    # Do not use a browser to work around an explicit access denial.
                    return None
                else:
                    page_url = result.final_url
                    text = _extract_text(result.body, page_url)
            if not browser_fallback and len(text.split()) < 80:
                try:
                    rendered_text, _ = await render_public_page(page_url, fetcher)
                    if len(rendered_text.split()) > len(text.split()):
                        text = rendered_text
                        browser_fallback = True
                except Exception:
                    pass
            if not text:
                return None
            if page is None:
                page = Page(product_id=product.id, url=page_url)
            elif page.url != page_url:
                page = session.exec(select(Page).where(Page.product_id == product.id, Page.url == page_url)).first() or page
                page.url = page_url
            page.status_code = result.status_code if result is not None else None
            page.fetch_method = "playwright" if browser_fallback else "httpx"
            page.text_hash = hashlib.sha256(text.encode()).hexdigest()
            page.cleaned_text = text
            page.fetched_at = utc_now()
            page.chosen_reason = item.reason
            page.screenshot_flag = item.screenshot
            session.add(page)
            session.commit()
            session.refresh(page)
        if page is None:
            return None
        pages_by_url[page_url] = page
        pages_by_url[original_url] = page
        used_urls.add(page_url)
        cached_facts = session.exec(select(Fact).where(Fact.product_id == product.id,
                                                       Fact.page_id == page.id,
                                                       Fact.verified == True)).all()
        if cached_facts:
            facts_output.extend({"id": fact.id, "field": fact.field,
                                 "value": (fact.value_json or {}).get("value", ""),
                                 "source_url": fact.source_url,
                                 "evidence_quote": fact.evidence_quote,
                                 "kind": fact.kind} for fact in cached_facts)
            return page_url
        extraction = await extract_facts(text, page_url, run_id=run_id, session=session)
        for fact in extraction.facts:
            existing = session.exec(select(Fact).where(Fact.product_id == product.id,
                Fact.source_url == page_url, Fact.field == fact.field,
                Fact.evidence_quote == fact.evidence_quote)).first()
            if existing is None:
                existing = Fact(product_id=product.id, page_id=page.id, field=fact.field,
                                value_json={"value": fact.value}, source_url=page_url,
                                evidence_quote=fact.evidence_quote, kind=fact.kind, verified=True)
                session.add(existing)
                session.commit()
                session.refresh(existing)
            facts_output.append({"id": existing.id, "field": fact.field, "value": fact.value,
                                 "source_url": page_url, "evidence_quote": fact.evidence_quote,
                                 "kind": fact.kind})
        return page_url

    for item in plan.pages:
        try:
            await research_page(item)
        except Exception as exc:
            page_errors.append(f"{item.url}: {type(exc).__name__}: {exc}")

    field_aliases = {
        "pricing": ("pricing", "price", "billing"),
        "integrations": ("integration",),
        "core features": ("feature", "capability"),
        "target users": ("target", "audience", "user"),
        "security and compliance": ("security", "compliance", "privacy"),
    }
    for _ in range(settings.max_gap_fill_iterations):
        observed = " ".join(fact["field"].lower() for fact in facts_output)
        missing = [label for label, aliases in field_aliases.items()
                   if not any(alias in observed for alias in aliases)]
        if not missing:
            break
        remaining = [info for info in compact_urls if str(info.url) not in used_urls]
        try:
            decision = await choose_gap_pages(missing, remaining, run_id=run_id, session=session)
        except Exception as exc:
            page_errors.append(f"gap-fill: {type(exc).__name__}: {exc}")
            break
        if not decision.urls:
            break
        from app.pipeline.schemas import PagePlan
        for url in decision.urls:
            try:
                await research_page(PagePlan(url=url, purpose="gap-fill", reason=decision.reasoning,
                                             priority=90, screenshot=False))
            except Exception as exc:
                page_errors.append(f"{url}: {type(exc).__name__}: {exc}")

    screenshot_urls = [homepage]
    screenshot_urls.extend(str(item.url) for item in plan.pages if item.screenshot)
    screenshot_urls = list(dict.fromkeys(screenshot_urls))
    for info in compact_urls:
        if len(screenshot_urls) >= settings.min_screenshots:
            break
        if str(info.url) not in screenshot_urls:
            screenshot_urls.append(str(info.url))
    # If the product has only one discoverable page, capture its top, middle,
    # and lower sections as distinct views instead of inventing more URLs.
    while screenshot_urls and len(screenshot_urls) < settings.min_screenshots:
        screenshot_urls.append(homepage)
    screenshot_urls = screenshot_urls[:settings.min_screenshots + 1]
    product.last_researched_at = utc_now()
    session.add(product)
    session.commit()
    return {"product_id": product.id, "candidate": candidate.model_dump(mode="json"),
            "facts": facts_output, "screenshot_urls": screenshot_urls,
            "screenshot_count": 0, "screenshot_errors": [], "page_errors": page_errors,
            "uncovered_topics": plan.uncovered_topics,
            "missing_fields": missing if "missing" in locals() else list(field_aliases)}


async def _capture_product_screenshots(product_result: dict, run_id: int, session: Session,
                                       fetcher: Fetcher) -> dict:
    """Capture and persist screenshots after product facts are collected."""
    settings = get_settings()
    candidate = Candidate.model_validate(product_result["candidate"])
    urls = product_result.get("screenshot_urls") or [str(candidate.url)]
    while urls and len(urls) < settings.min_screenshots:
        urls.append(str(candidate.url))
    screenshot_errors: list[str] = []
    screenshot_results = []
    try:
        screenshot_results = await capture_screenshots(
            urls,
            re.sub(r"[^a-z0-9-]+", "-", candidate.name.lower()).strip("-")[:50],
            fetcher, minimum=settings.min_screenshots, run_id=run_id)
    except Exception as exc:
        screenshot_errors.append(f"{type(exc).__name__}: {exc}")
        screenshot_results = getattr(exc, "results", [])
    for shot in screenshot_results:
        page = session.exec(select(Page).where(Page.product_id == product_result["product_id"],
                                                Page.url == shot.url)).first()
        if page and shot.path:
            existing = session.exec(select(Screenshot).where(Screenshot.page_id == page.id,
                                                              Screenshot.path == shot.path)).first()
            record = existing or Screenshot(page_id=page.id, path=shot.path, width=shot.width,
                                            height=shot.height, caption=shot.caption)
            record.width = shot.width
            record.height = shot.height
            record.caption = shot.caption
            record.accepted = shot.accepted
            session.add(record)
        elif shot.path:
            # A screenshot target may come from a sitemap even when page picking
            # did not choose it for text extraction. Persist it as a screenshot
            # page so every accepted capture can appear in the final report.
            page = Page(product_id=product_result["product_id"], url=shot.url,
                        fetch_method="playwright_screenshot", chosen_reason="screenshot capture",
                        screenshot_flag=True)
            session.add(page)
            session.commit()
            session.refresh(page)
            session.add(Screenshot(page_id=page.id, path=shot.path, width=shot.width,
                                   height=shot.height, caption=shot.caption, accepted=shot.accepted))
    session.commit()
    return {**product_result,
            "screenshot_count": sum(item.accepted for item in screenshot_results),
            "screenshot_errors": screenshot_errors,
            "screenshot_checks": [{"url": item.url, "accepted": item.accepted,
                                   "caption": item.caption, "issue": item.issue}
                                  for item in screenshot_results]}


async def run_pipeline(run_id: int) -> None:
    """Execute a persisted pipeline in a background task, resuming completed stages."""
    fetcher = Fetcher()
    with Session(engine) as session:
        run = session.get(Run, run_id)
        if run is None:
            await fetcher.close()
            return
        category = run.category
        settings = get_settings()
        try:
            provider = configured_search_provider()
        except Exception as exc:
            run.status = "failed"
            run.error = f"{type(exc).__name__}: {exc}"
            run.finished_at = utc_now()
            session.add(run)
            session.commit()
            await fetcher.close()
            return

        async def discover_stage(_state: dict) -> dict:
            all_candidates: dict[str, Candidate] = {}
            existing_domains = set(session.exec(select(Product.canonical_domain)).all())
            prior_discoveries = session.exec(
                select(StageLog).join(Run, StageLog.run_id == Run.id)
                .where(Run.category == category, StageLog.stage == "discover")
                .order_by(StageLog.id.desc()).limit(8)
            ).all()
            query_history = [query for stage in prior_discoveries
                             for query in (stage.output_json or {}).get("queries", [])][:20]
            discovery_result = await discover(category, provider, days=90,
                                               previous_queries=query_history,
                                               run_id=run_id, session=session,
                                               excluded_domains=existing_domains)
            if len(discovery_result) == 3:
                candidates, queries, diagnostics = discovery_result
            else:  # Keep injected/older discovery adapters compatible.
                candidates, queries = discovery_result
                diagnostics = {"candidate_count": len(candidates)}
            for candidate in candidates:
                domain = _domain_for(str(candidate.url))
                if domain not in existing_domains:
                    all_candidates.setdefault(domain, candidate)
            return {"candidates": [item.model_dump(mode="json") for item in all_candidates.values()],
                    "queries": queries, "diagnostics": diagnostics,
                    "comparison_scope": diagnostics.get("comparison_scope", "recent_launches"),
                    "scope_note": diagnostics.get("fallback_note")}

        async def validate_stage(state: dict) -> dict:
            raw = state["discover"]["candidates"]
            reports = []
            valid = []
            for record in raw:
                candidate = Candidate.model_validate(record)
                result = await validate_candidate(candidate, fetcher)
                reports.append({"name": candidate.name, "valid": result.valid, "reason": result.reason,
                                "word_count": result.word_count, "url": result.final_url})
                if result.valid:
                    valid.append(candidate.model_dump(mode="json"))
            comparison_scope = state["discover"].get("comparison_scope", "recent_launches")
            scope_note = state["discover"].get("scope_note")
            if len(valid) < 2 and settings.allow_established_alternatives:
                excluded = set(session.exec(select(Product.canonical_domain)).all())
                excluded.update(_domain_for(str(item["url"])) for item in valid)
                alternative_result = await discover(
                    category, provider, days=365, run_id=run_id, session=session,
                    excluded_domains=excluded, force_established_alternatives=True)
                if len(alternative_result) == 3:
                    alternatives, _, alternative_diagnostics = alternative_result
                else:
                    alternatives, _ = alternative_result
                    alternative_diagnostics = {"candidate_count": len(alternatives)}
                for candidate in alternatives:
                    if _domain_for(str(candidate.url)) in excluded:
                        continue
                    result = await validate_candidate(candidate, fetcher)
                    reports.append({"name": candidate.name, "valid": result.valid,
                                    "reason": result.reason, "word_count": result.word_count,
                                    "url": result.final_url, "comparison_scope": "category_alternatives"})
                    if result.valid:
                        valid.append(candidate.model_dump(mode="json"))
                if len(valid) >= 2 and alternatives:
                    comparison_scope = "category_alternatives"
                    scope_note = (alternative_diagnostics.get("fallback_note") or
                                  "Fewer than two reachable recent launches were found. "
                                  "This report compares current category alternatives; launch recency is not verified.")
            return {"candidates": valid, "checks": reports,
                    "comparison_scope": comparison_scope, "scope_note": scope_note}

        async def select_stage(state: dict) -> dict:
            candidates = [Candidate.model_validate(item) for item in state["validate"]["candidates"]]
            discovery = state["discover"]
            existing = set(session.exec(select(Product.canonical_domain)).all())
            unseen = [candidate for candidate in candidates
                      if candidate.is_product_site and _domain_for(str(candidate.url)) not in existing]
            if len(unseen) < 2:
                checks = state["validate"].get("checks", [])
                rejected = [f"{item.get('name', 'Candidate')}: {item.get('reason', 'validation failed')}"
                            for item in checks if not item.get("valid")][:3]
                non_products = sum(not item.is_product_site for item in candidates)
                details = []
                if rejected:
                    details.append("Validation rejected " + "; ".join(rejected))
                if non_products:
                    details.append(f"{non_products} validated candidate(s) were directory or non-product pages")
                diagnostics = discovery.get("diagnostics", {})
                windows = diagnostics.get("windows", [])
                window_summary = "; ".join(
                    f"{item.get('days')}d: {item.get('search_results', 0)} results, "
                    f"{item.get('extractor_candidates', 0)} extracted, "
                    f"{item.get('grounded_candidates', 0)} grounded"
                    for item in windows
                )
                rejected = {}
                for item in windows:
                    for reason, count in item.get("rejected", {}).items():
                        rejected[reason] = rejected.get(reason, 0) + count
                homepage_search_count = sum(item.get("homepage_searches", 0) for item in windows)
                rejection_summary = ", ".join(f"{reason}: {count}" for reason, count in rejected.items() if count)
                details.append("Discovery diagnostics: " + (window_summary or "no search results")
                               + (f"; {homepage_search_count} official-homepage lookup(s)"
                                  if homepage_search_count else "")
                               + (f". Candidate filters — {rejection_summary}" if rejection_summary else ""))
                detail_text = " " + ". ".join(details) + "."
                raise ValueError(
                    f"At least two new, validated product sites are required for a comparison. Found {len(unseen)}. "
                    f"{len(discovery.get('candidates', []))} candidate(s) passed grounding and "
                    f"{len(candidates)} passed site validation.{detail_text}"
                )
            first, second, backups, reasoning = await select_pair(candidates, existing, run_id=run_id,
                session=session,
                    established_alternatives=state["validate"].get("comparison_scope") == "category_alternatives")
            return {"selected": [first.model_dump(mode="json"), second.model_dump(mode="json")],
                    "backups": [item.model_dump(mode="json") for item in backups], "reasoning": reasoning,
                    "comparison_scope": state["validate"].get("comparison_scope", "recent_launches"),
                    "scope_note": state["validate"].get("scope_note")}

        async def research_stage(state: dict) -> dict:
            if len(state["select"].get("selected", [])) < 2:
                reason = state["select"].get("reasoning", "Not enough new validated candidates were found.")
                count = state["select"].get("available_candidates", 0)
                return {"products": [], "partial": True, "error": f"{reason} Found {count}."}
            chosen = [Candidate.model_validate(item) for item in state["select"]["selected"]]
            backups = [Candidate.model_validate(item) for item in state["select"]["backups"]]
            results = []
            used_domains: set[str] = set()
            for index, original in enumerate(chosen):
                attempts = [original] + [item for item in backups if _domain_for(str(item.url)) not in
                                        {_domain_for(str(x.url)) for x in chosen} | used_domains]
                result = None
                failure = None
                for candidate in attempts:
                    try:
                        result = await _research_product(candidate, category, run_id, session, fetcher)
                        used_domains.add(_domain_for(str(candidate.url)))
                        break
                    except Exception as exc:
                        failure = f"{candidate.name}: {type(exc).__name__}: {exc}"
                if result is None:
                    # Preserve the selected candidate identity in a partial report, with no fabricated facts.
                    product = _upsert_product(session, original)
                    result = {"product_id": product.id, "candidate": original.model_dump(mode="json"),
                              "facts": [], "screenshot_count": 0, "screenshot_errors": [failure or "Research failed"],
                              "uncovered_topics": ["research unavailable"]}
                results.append(result)
            return {"products": results,
                    "partial": any(item.get("page_errors")
                                   or not item["facts"]
                                   for item in results)}

        async def screenshot_stage(state: dict) -> dict:
            research_products = state["research"].get("products", [])
            if len(research_products) < 2:
                return {"products": research_products, "partial": True, "_stage_failed": True,
                        "error": state["research"].get("error", "No two products reached screenshot capture.")}
            results = []
            for product in research_products:
                candidate = Candidate.model_validate(product["candidate"])
                try:
                    captured = await _capture_product_screenshots(product, run_id, session, fetcher)
                except Exception as exc:
                    captured = {**product, "screenshot_count": 0,
                                "screenshot_errors": [f"{type(exc).__name__}: {exc}"],
                                "screenshot_checks": []}
                results.append(captured)
                logger.info("Screenshot stage finished for product",
                            extra={"run_id": run_id, "product": candidate.name,
                                   "accepted_screenshots": captured["screenshot_count"],
                                   "required_screenshots": settings.min_screenshots})
            partial = any(item["screenshot_count"] < settings.min_screenshots
                          or item["screenshot_errors"] for item in results)
            return {"products": results, "partial": partial, "_stage_failed": partial}

        async def compare_stage(state: dict) -> dict:
            products = state["screenshot_capture"]["products"]
            if len(products) < 2:
                return {"partial": True, "error": state["screenshot_capture"].get(
                    "error", "A comparison requires two researched products.")}
            first, second = products
            candidate_a = Candidate.model_validate(first["candidate"])
            candidate_b = Candidate.model_validate(second["candidate"])
            comparison_data = None
            comparison_error = None
            try:
                if not first["facts"] and not second["facts"]:
                    raise ValueError("No verified product facts were collected")
                comparison_data, markdown = await compare_products(candidate_a.name, first["facts"],
                    candidate_b.name, second["facts"], run_id=run_id, session=session)
            except Exception as exc:
                comparison_error = f"{type(exc).__name__}: {exc}"
                markdown = (f"# Partial research report: {candidate_a.name} vs {candidate_b.name}\n\n"
                            "Evidence collection finished, but the comparison narrative could not be completed. "
                            "The verified observations below are provided without a winner or recommendation.\n")
                for label, product in ((candidate_a.name, first), (candidate_b.name, second)):
                    markdown += f"\n## Verified observations for {label}\n"
                    if product["facts"]:
                        markdown += "\n".join(f"- {fact['field']}: {fact['value']} ([F{fact['id']}], [source]({fact['source_url']}))"
                                               for fact in product["facts"])
                    else:
                        markdown += "No verified facts were collected."
                    if product["screenshot_errors"]:
                        markdown += f"\n\nResearch notes: {', '.join(product['screenshot_errors'])}"
            if not comparison_error:
                markdown += "\n\n## Sources\n"
                sources = sorted({fact["source_url"] for product in products for fact in product["facts"]})
                markdown += "\n".join(f"- [{url}]({url})" for url in sources)
            scope = state["select"].get("comparison_scope", state["validate"].get(
                "comparison_scope", state["discover"].get("comparison_scope", "recent_launches")))
            scope_note = state["select"].get("scope_note") or state["validate"].get("scope_note")
            if scope == "category_alternatives":
                disclosure = (scope_note or "This comparison uses current category alternatives; launch recency was not verified.")
                markdown = f"> **Category alternatives:** {disclosure}\n\n" + markdown
            research_partial = any(item.get("page_errors") or not item.get("facts") for item in products)
            report_is_partial = (research_partial or state["screenshot_capture"]["partial"]
                                 or comparison_error is not None)
            if report_is_partial:
                markdown = "> **Partial report:** the pipeline could not meet all research or screenshot requirements.\n\n" + markdown
            screenshots = []
            markdown_screenshots = []
            for product in products:
                product_row = session.get(Product, product["product_id"])
                if product_row:
                    rows = session.exec(select(Screenshot).join(Page).where(Page.product_id == product_row.id,
                                                                              Screenshot.accepted == True)).all()
                    for row in rows:
                        screenshots.append({"path": row.path, "caption": row.caption,
                                            "product": product_row.name})
                        try:
                            relative_path = Path(row.path).resolve().relative_to(Path(settings.screenshots_dir).resolve())
                            markdown_screenshots.append((product_row.name, row.caption, relative_path.as_posix()))
                        except ValueError:
                            continue
            if markdown_screenshots:
                markdown += "\n\n## Screenshots\n"
                for name, caption, relative_path in markdown_screenshots:
                    markdown += f"\n### {name}\n\n![{caption}](/screenshots/{relative_path})\n\n_{caption}_\n"
            benchmarks = [row.model_dump(mode="json") for row in getattr(comparison_data, "rows", [])]
            report_facts = [dict(fact, product=Candidate.model_validate(product["candidate"]).name)
                            for product in products for fact in product["facts"]]
            html = render_html(markdown, f"{candidate_a.name} vs {candidate_b.name}",
                               screenshots=screenshots, benchmarks=benchmarks,
                               product_names=(candidate_a.name, candidate_b.name), facts=report_facts,
                               scope_label=("Current category alternatives · launch recency unverified"
                                            if scope == "category_alternatives" else "Verified recent launches"),
                               scope_note=scope_note or "")
            comparison = Comparison(run_id=run_id, product_a_id=first["product_id"], product_b_id=second["product_id"],
                                    markdown=markdown, html=html,
                                    confidence_notes=(comparison_data.confidence_notes if comparison_data else
                                                     comparison_error or "No verified comparison facts were available."))
            session.add(comparison)
            session.commit()
            session.refresh(comparison)
            return {"comparison_id": comparison.id, "partial": report_is_partial}

        try:
            state = await execute_stages(run_id,
                [("discover", discover_stage), ("validate", validate_stage), ("select", select_stage),
                 ("research", research_stage), ("screenshot_capture", screenshot_stage),
                 ("compare_render", compare_stage)], session)
            if state.get("compare_render", {}).get("partial"):
                run = session.get(Run, run_id)
                run.status = "partial"
                run.error = state["compare_render"].get("error")
                session.add(run)
                session.commit()
        except Exception as exc:
            logger.exception("Pipeline run failed", extra={"run_id": run_id})
            current = session.get(Run, run_id)
            if current is not None and current.status == "running":
                current.status = "partial"
                current.error = f"{type(exc).__name__}: {exc}"
                current.finished_at = utc_now()
                session.add(current)
                session.commit()
        finally:
            await fetcher.close()
