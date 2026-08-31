from app.main import robots, sitemap


def test_robots_allows_homepage_and_points_to_sitemap() -> None:
    response = robots()
    body = response.body.decode("utf-8")

    assert response.media_type == "text/plain"
    assert "Allow: /\n" in body
    assert "Disallow: /admin\n" in body
    assert "Disallow: /api/\n" in body
    assert "Sitemap: https://propioscan.com/sitemap.xml\n" in body


def test_sitemap_lists_only_the_canonical_public_page() -> None:
    response = sitemap()
    body = response.body.decode("utf-8")

    assert response.media_type == "application/xml"
    assert body.count("<url>") == 1
    assert "<loc>https://propioscan.com/</loc>" in body
    assert "<lastmod>2026-08-31</lastmod>" in body
    assert "/admin" not in body
    assert "/api/" not in body
