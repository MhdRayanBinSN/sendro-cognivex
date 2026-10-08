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
