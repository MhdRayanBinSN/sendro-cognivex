import gzip

from app.pipeline.sitemap import parse_sitemap


def test_parse_urlset_and_lastmod():
    xml = b'''<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
      <url><loc>https://example.com/pricing</loc><lastmod>2026-01-01</lastmod></url>
    </urlset>'''
    parsed = parse_sitemap(xml)
    assert parsed.urls[0].url == "https://example.com/pricing"
    assert parsed.urls[0].lastmod == "2026-01-01"


def test_parse_gzipped_sitemap_index():
    xml = b'''<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
      <sitemap><loc>https://example.com/pages.xml</loc></sitemap>
    </sitemapindex>'''
    parsed = parse_sitemap(gzip.compress(xml))
    assert parsed.child_sitemaps == ("https://example.com/pages.xml",)
