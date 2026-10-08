from datetime import date

from app.pipeline.discover import _is_content_page, _supports_recent_launch
from app.services.search import SearchResult


def test_recent_launch_requires_launch_language_and_dated_result():
    dated_release = SearchResult(
        title="Anima launches AI website clone",
        url="https://example.com/launch",
        snippet="Anima launches its website cloning feature today.",
        source="tavily",
        published_date="2026-09-15T12:00:00Z",
    )
    listicle = SearchResult(
        title="7 Best AI Website Builders in 2026",
        url="https://example.com/list",
        snippet="Compare popular AI website tools.",
        source="tavily",
        published_date="2026-09-15",
    )
    assert _supports_recent_launch("Anima launches its website cloning feature", dated_release,
                                   date(2026, 7, 1), date(2026, 10, 8))
    assert not _supports_recent_launch("7 Best AI Website Builders in 2026", listicle,
                                       date(2026, 7, 1), date(2026, 10, 8))
    assert not _supports_recent_launch("Anima launches its website cloning feature",
                                       SearchResult(**{**dated_release.__dict__, "published_date": None}),
                                       date(2026, 7, 1), date(2026, 10, 8))


def test_article_and_documentation_urls_are_not_product_homepages():
    assert _is_content_page("https://example.com/blog/launch")
    assert _is_content_page("https://learn.example.com/product")
    assert not _is_content_page("https://example.com/clone-website")
