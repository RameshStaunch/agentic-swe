"""Integration tests against a real Postgres (SHORTENER_DATABASE_URL, default the local pixi cluster)."""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from shortener.app import app


@pytest.fixture
def client():
    with TestClient(app) as c:
        async def reset():
            async with app.state.engine.begin() as conn:
                await conn.execute(text("TRUNCATE clicks, links RESTART IDENTITY"))
        c.portal.call(reset)
        yield c


def test_shorten_and_redirect(client):
    created = client.post("/links", json={"url": "https://example.com/a?b=1"})
    assert created.status_code == 201
    body = created.json()
    assert body["short_url"].endswith(body["code"])
    r = client.get(f"/{body['code']}", follow_redirects=False)
    assert r.status_code == 307
    assert r.headers["location"] == "https://example.com/a?b=1"


def test_unknown_code_is_404(client):
    assert client.get("/nope123", follow_redirects=False).status_code == 404


def test_rejects_non_http_urls(client):
    assert client.post("/links", json={"url": "javascript:alert(1)"}).status_code == 422


def test_custom_alias_and_conflict(client):
    assert client.post("/links", json={"url": "https://example.com", "alias": "promo"}).json()["code"] == "promo"
    assert client.post("/links", json={"url": "https://example.org", "alias": "promo"}).status_code == 409
    assert client.post("/links", json={"url": "https://example.org", "alias": "links"}).status_code == 422


def test_expired_link_is_410(client):
    soon = (datetime.now(UTC) + timedelta(seconds=1)).isoformat()
    code = client.post("/links", json={"url": "https://example.com", "expires_at": soon}).json()["code"]

    async def expire():
        async with app.state.engine.begin() as conn:
            await conn.execute(text("UPDATE links SET expires_at = now() - interval '1 minute' WHERE code = :c"), {"c": code})
    client.portal.call(expire)
    assert client.get(f"/{code}", follow_redirects=False).status_code == 410


def test_past_expiry_rejected(client):
    past = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    assert client.post("/links", json={"url": "https://example.com", "expires_at": past}).status_code == 422


def test_stats_count_clicks_and_referrers(client):
    code = client.post("/links", json={"url": "https://example.com"}).json()["code"]
    for ref in ["https://news.example", "https://news.example", None]:
        client.get(f"/{code}", follow_redirects=False, headers={"referer": ref} if ref else {})
    stats = client.get(f"/links/{code}/stats").json()
    assert stats["total_clicks"] == 3
    assert sum(d["clicks"] for d in stats["clicks_by_day"]) == 3
    assert stats["top_referrers"][0] == ["https://news.example", 2]


def test_stats_unknown_code(client):
    assert client.get("/links/nope/stats").status_code == 404
