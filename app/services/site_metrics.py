"""Optional public GitHub and PageSpeed signals for the selected products."""

import asyncio
import re
from datetime import datetime, timezone
from urllib.parse import quote, urlsplit

import httpx

from app.config import get_settings
from app.pipeline.schemas import Candidate
from app.services.search import GitHubSearchProvider
from app.services.urlsafe import registered_domain, validate_public_url

PAGESPEED_ENDPOINT = "https://www.googleapis.com/pagespeedonline/v5/runPagespeed"
IGNORED_REPO_TOKENS = {"ai", "app", "the", "official", "website", "web", "tool", "tools",
                       "clone", "cloner", "builder", "software", "platform"}


def _provider_error(exc: Exception, provider: str) -> str:
    """Return a short quota/error diagnostic without leaking configured credentials."""
    response = getattr(exc, "response", None)
    status = getattr(response, "status_code", None)
    message = None
    if response is not None:
        try:
            payload = response.json()
            error = payload.get("error", payload) if isinstance(payload, dict) else {}
            if isinstance(error, dict):
                message = error.get("message") or error.get("status")
        except Exception:
            message = None
    label = {"github": "GitHub", "pagespeed": "PageSpeed"}.get(provider, provider)
    if not message:
        return f"{label} {type(exc).__name__}"
    message = re.sub(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", "this network", str(message))
    message = re.sub(r"\s+for consumer 'project_number:\d+'", "", message)
    message = message.split(" (", 1)[0]
    settings = get_settings()
    for secret in (settings.github_token, settings.pagespeed_api_key):
        if secret:
            value = secret.get_secret_value()
            if value:
                message = message.replace(value, "[redacted]")
    return f"{label} HTTP {status}: {message[:220]}" if status else f"{label}: {message[:220]}"


def _tokens(value: str) -> set[str]:
    return {token for token in re.findall(r"[a-z0-9]+", value.casefold())
            if token not in IGNORED_REPO_TOKENS}


def _match_repository(candidate: Candidate, results: list) -> dict | None:
    target_tokens = _tokens(candidate.name)
    product_domain = registered_domain(candidate.url.host or "")
    ranked: list[tuple[float, int, object]] = []
    for result in results:
        meta = result.metadata
        repo_tokens = _tokens(meta.get("full_name", "").split("/", 1)[-1])
        homepage = meta.get("homepage")
        homepage_domain = ""
        if homepage:
            try:
                homepage_domain = registered_domain(urlsplit(homepage).hostname or "")
            except Exception:
                homepage_domain = ""
        domain_match = bool(product_domain and homepage_domain == product_domain)
        overlap = len(target_tokens & repo_tokens) / max(1, len(target_tokens))
        exact_name = bool(target_tokens and target_tokens.issubset(repo_tokens))
        confidence = 1.0 if domain_match else (0.85 if exact_name else overlap)
        if confidence >= 0.67:
            ranked.append((confidence, int(meta.get("stars", 0)), result))
    if not ranked:
        return None
    confidence, _, result = max(ranked, key=lambda item: (item[0], item[1]))
    return {"repository": result.metadata.get("full_name"), "url": result.url,
            "stars": result.metadata.get("stars", 0), "forks": result.metadata.get("forks", 0),
            "language": result.metadata.get("language"), "license": result.metadata.get("license"),
            "topics": result.metadata.get("topics", []), "updated_at": result.metadata.get("updated_at"),
            "archived": result.metadata.get("archived", False),
            "match_confidence": round(confidence, 2),
            "source": "GitHub public repository search"}


async def _github_signal(candidate: Candidate) -> dict | None:
    settings = get_settings()
    token = settings.github_token.get_secret_value() if settings.github_token else None
    results = await GitHubSearchProvider(token).search(candidate.name)
    return _match_repository(candidate, results)


async def _pagespeed_signal(candidate: Candidate) -> dict:
    settings = get_settings()
    safe_url = await validate_public_url(str(candidate.url))
    params: list[tuple[str, str]] = [
        ("url", safe_url), ("strategy", "mobile"), ("locale", "en"),
        ("category", "seo"), ("category", "performance"),
    ]
    if settings.pagespeed_api_key:
        api_key = settings.pagespeed_api_key.get_secret_value()
        if api_key:
            params.append(("key", api_key))
    async with httpx.AsyncClient(timeout=60, trust_env=False) as client:
        response = await client.get(PAGESPEED_ENDPOINT, params=params)
        response.raise_for_status()
        payload = response.json()
    lighthouse = payload.get("lighthouseResult") or {}
    categories = lighthouse.get("categories") or {}
    seo_category = categories.get("seo") or {}
    performance_category = categories.get("performance") or {}
    audits = lighthouse.get("audits") or {}
    failures = []
    for audit_ref in seo_category.get("auditRefs", []):
        audit = audits.get(audit_ref.get("id"), {})
        score = audit.get("score")
        if isinstance(score, (int, float)) and score < 1:
            failures.append({"id": audit.get("id", audit_ref.get("id")),
                             "title": audit.get("title", audit_ref.get("id")),
                             "score": score, "display_value": audit.get("displayValue")})
    return {
        "url": lighthouse.get("finalUrl") or safe_url,
        "strategy": "mobile",
        "seo_score": round(float(seo_category["score"]) * 100)
            if isinstance(seo_category.get("score"), (int, float)) else None,
        "performance_score": round(float(performance_category["score"]) * 100)
            if isinstance(performance_category.get("score"), (int, float)) else None,
        "failed_seo_audits": failures[:8],
        "fetch_time": lighthouse.get("fetchTime"),
        "lighthouse_version": lighthouse.get("lighthouseVersion"),
        "report_url": "https://pagespeed.web.dev/analysis?url=" + quote(safe_url, safe=""),
        "source": "Google PageSpeed Insights / Lighthouse",
    }


async def collect_site_metrics(candidates: list[Candidate]) -> list[dict]:
    """Collect optional signals independently; one provider failure doesn't fail a run."""
    settings = get_settings()

    async def collect(candidate: Candidate) -> dict:
        github = None
        github_error = None
        pagespeed = None
        pagespeed_error = None
        errors: list[str] = []
        work = []
        if settings.github_search_enabled:
            work.append(("github", _github_signal(candidate)))
        else:
            github_error = "Disabled in configuration."
        if settings.pagespeed_audit_enabled:
            work.append(("pagespeed", _pagespeed_signal(candidate)))
        else:
            pagespeed_error = "Disabled in configuration."
        outcomes = await asyncio.gather(*(item[1] for item in work), return_exceptions=True)
        for (name, _), outcome in zip(work, outcomes):
            if isinstance(outcome, Exception):
                diagnostic = _provider_error(outcome, name)
                errors.append(diagnostic)
                if name == "github":
                    github_error = diagnostic
                else:
                    pagespeed_error = diagnostic
            elif name == "github":
                github = outcome
            else:
                pagespeed = outcome
        return {"product": candidate.name, "homepage_url": str(candidate.url),
                "github": github, "github_error": github_error,
                "pagespeed": pagespeed, "pagespeed_error": pagespeed_error, "errors": errors,
                "checked_at": datetime.now(timezone.utc).isoformat()}

    return await asyncio.gather(*(collect(candidate) for candidate in candidates))
