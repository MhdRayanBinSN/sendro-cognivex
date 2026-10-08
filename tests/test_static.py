from app.main import app, favicon, static_asset


def test_favicon_endpoints_serve_declared_icon():
    legacy_icon = favicon()
    svg_icon = static_asset("favicon.svg")

    assert legacy_icon.status_code == 200
    assert legacy_icon.media_type == "image/svg+xml"
    assert b"<svg" in legacy_icon.body
    assert svg_icon.status_code == 200
    assert svg_icon.media_type == "image/svg+xml"
    assert any(route.path == "/favicon.ico" for route in app.routes)
