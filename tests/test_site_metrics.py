from types import SimpleNamespace

import pytest
from pydantic import SecretStr

from app.pipeline.schemas import Candidate
from app.services import search, site_metrics


@pytest.mark.asyncio
async def test_github_search_maps_public_repo_signals(monkeypatch):
    requests = []

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"items": [{
                "full_name": "acme/justcopy", "html_url": "https://github.com/acme/justcopy",
                "description": "AI website builder", "stargazers_count": 42, "forks_count": 7,
                "language": "TypeScript", "license": {"spdx_id": "MIT"},
                "topics": ["website-builder"], "homepage": "https://justcopy.ai",
                "created_at": "2024-01-01T00:00:00Z", "updated_at": "2026-10-01T00:00:00Z",
                "archived": False,
            }]}

    class Client:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): return None
        async def get(self, url, **kwargs):
            requests.append((url, kwargs))
            return Response()

    monkeypatch.setattr(search.httpx, "AsyncClient", Client)
    result = await search.GitHubSearchProvider("test-token").search("JustCopy AI")

    assert len(result) == 1
    assert result[0].source == "github"
    assert result[0].metadata["stars"] == 42
    assert result[0].metadata["homepage"] == "https://justcopy.ai"
    assert requests[0][1]["headers"]["Authorization"] == "Bearer test-token"
    assert requests[0][1]["params"]["per_page"] == 10


def test_github_metadata_is_attached_only_to_a_confident_product_match():
    candidate = Candidate(name="JustCopy AI", url="https://justcopy.ai", is_product_site=True,
                          confidence_new=0.0, is_recent_launch=False)
    result = search.SearchResult(
        title="acme/justcopy · GitHub repository", url="https://github.com/acme/justcopy",
        snippet="AI website builder", source="github",
        metadata={"full_name": "acme/justcopy", "stars": 42, "forks": 7,
                  "homepage": "https://justcopy.ai", "updated_at": "2026-10-01T00:00:00Z"},
    )
    unrelated = search.SearchResult(
        title="acme/other · GitHub repository", url="https://github.com/acme/other",
        snippet="Unrelated", source="github", metadata={"full_name": "acme/other", "stars": 10000},
    )

    matched = site_metrics._match_repository(candidate, [unrelated, result])
    assert matched and matched["repository"] == "acme/justcopy"
    assert matched["match_confidence"] == 1.0
    assert site_metrics._match_repository(candidate, [unrelated]) is None


def test_provider_quota_errors_are_actionable_and_redact_secrets(monkeypatch):
    monkeypatch.setattr(site_metrics, "get_settings", lambda: SimpleNamespace(
        github_token=SecretStr("private-github-token"), pagespeed_api_key=None))

    class Response:
        status_code = 403
        def json(self):
            return {"message": "API rate limit exceeded for 192.0.2.10. (Use a token)"}

    exc = type("RequestError", (Exception,), {"response": Response()})()
    message = site_metrics._provider_error(exc, "github")

    assert "GitHub HTTP 403" in message
    assert "rate limit exceeded" in message
    assert "192.0.2.10" not in message
    assert "private-github-token" not in message


@pytest.mark.asyncio
async def test_pagespeed_parses_mobile_seo_and_performance_scores(monkeypatch):
    settings = SimpleNamespace(pagespeed_api_key=SecretStr("psi-key"))
    monkeypatch.setattr(site_metrics, "get_settings", lambda: settings)
    monkeypatch.setattr(site_metrics, "validate_public_url", lambda url: _immediate(url))
    calls = []

    class Response:
        def raise_for_status(self): return None
        def json(self):
            return {"lighthouseResult": {
                "finalUrl": "https://product.example/", "fetchTime": "2026-10-08T00:00:00Z",
                "lighthouseVersion": "13.0", "categories": {
                    "seo": {"score": 0.92, "auditRefs": [{"id": "meta-description"}]},
                    "performance": {"score": 0.81},
                }, "audits": {"meta-description": {"score": 0, "title": "Meta description",
                                                       "displayValue": "Missing"}},
            }}

    class Client:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): return None
        async def get(self, url, **kwargs):
            calls.append((url, kwargs["params"]))
            return Response()

    monkeypatch.setattr(site_metrics.httpx, "AsyncClient", Client)
    candidate = Candidate(name="Product", url="https://product.example", is_product_site=True,
                          confidence_new=0.5)
    result = await site_metrics._pagespeed_signal(candidate)

    assert result["seo_score"] == 92
    assert result["performance_score"] == 81
    assert result["failed_seo_audits"][0]["id"] == "meta-description"
    assert result["strategy"] == "mobile"
    assert ("category", "seo") in calls[0][1]
    assert ("category", "performance") in calls[0][1]
    assert ("key", "psi-key") in calls[0][1]


@pytest.mark.asyncio
async def test_disabled_integrations_are_reported_as_disabled(monkeypatch):
    monkeypatch.setattr(site_metrics, "get_settings", lambda: SimpleNamespace(
        github_search_enabled=False, pagespeed_audit_enabled=False,
        github_token=None, pagespeed_api_key=None))
    candidate = Candidate(name="Product", url="https://product.example", is_product_site=True,
                          confidence_new=0.5)

    result, = await site_metrics.collect_site_metrics([candidate])

    assert result["github"] is None
    assert result["pagespeed"] is None
    assert result["github_error"] == "Disabled in configuration."
    assert result["pagespeed_error"] == "Disabled in configuration."


async def _immediate(value):
    return value
