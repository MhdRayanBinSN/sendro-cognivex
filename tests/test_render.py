from app.pipeline.render import render_html


def test_render_disables_raw_html_and_embeds_screenshot_data(tmp_path):
    image = tmp_path / "shot.png"
    image.write_bytes(b"fake png bytes")
    html = render_html("<script>alert(1)</script>\n\n# Report", "Research",
                       screenshots=[{"path": str(image), "caption": "Pricing page", "product": "Product A"}])
    assert "<script>alert(1)</script>" not in html
    assert "data:image/png;base64," in html
    assert "Pricing page" in html


def test_render_adds_visual_evidence_backed_benchmarks():
    html = render_html("# Comparison", "Alpha vs Beta",
                       benchmarks=[{"dimension": "Features", "score_a": 80, "score_b": 60}],
                       product_names=("Alpha", "Beta"))

    assert "Evidence-backed benchmarks" in html
    assert "width:80.0%" in html
    assert "width:60.0%" in html
    assert "Comparison at a glance" in html


def test_render_explains_when_fair_numeric_benchmarks_are_unavailable():
    html = render_html("# Comparison", "Alpha vs Beta",
                       benchmarks=[{"dimension": "Pricing", "score_a": None, "score_b": None}],
                       product_names=("Alpha", "Beta"))

    assert "Evidence-backed benchmarks" in html
    assert "No numeric scores were assigned" in html


def test_render_shows_technical_seo_and_github_signals():
    html = render_html(
        "# Comparison", "Alpha vs Beta", product_names=("Alpha", "Beta"),
        site_metrics=[{
            "product": "Alpha",
            "pagespeed": {"seo_score": 92, "performance_score": 81,
                          "failed_seo_audits": [{"title": "Meta description", "display_value": "Missing"}],
                          "report_url": "https://pagespeed.web.dev/analysis?url=https%3A%2F%2Falpha.example"},
            "github": {"repository": "acme/alpha", "url": "https://github.com/acme/alpha",
                       "stars": 42, "forks": 7, "language": "Python", "license": "MIT",
                       "updated_at": "2026-10-01", "match_confidence": 1.0},
            "errors": [],
        }],
    )

    assert "Technical SEO and GitHub signals" in html
    assert "92/100" in html and "81/100" in html
    assert "Meta description" in html
    assert "acme/alpha" in html and "42" in html
    assert "not keyword rankings or traffic estimates" in html
