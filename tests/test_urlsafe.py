import pytest

from app.services.urlsafe import UnsafeURLError, canonicalize_url, registered_domain, validate_public_url


def test_canonicalize_removes_tracking_and_fragment():
    assert canonicalize_url("HTTPS://Example.com/a/?utm_source=x&ok=1#top") == "https://example.com/a?ok=1"


@pytest.mark.parametrize("url", ["file:///etc/passwd", "//example.com/path", "https://user:pass@example.com"])
def test_canonicalize_rejects_unsafe_urls(url):
    with pytest.raises(UnsafeURLError):
        canonicalize_url(url)


@pytest.mark.asyncio
@pytest.mark.parametrize("url", ["http://127.0.0.1/", "http://169.254.169.254/latest/meta-data/"])
async def test_public_url_guard_rejects_private_ip_literals(url):
    with pytest.raises(UnsafeURLError):
        await validate_public_url(url)


def test_registered_domain_collapses_subdomains():
    assert registered_domain("docs.example.co.uk") == "example.co.uk"
