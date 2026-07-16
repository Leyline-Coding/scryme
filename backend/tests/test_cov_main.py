"""Coverage for src/main.py — lifespan, cache headers, and the LAN guard middleware."""

import pytest
import src.main as main
from httpx import ASGITransport, AsyncClient


@pytest.mark.asyncio
async def test_lifespan_skips_scheduler_under_test(monkeypatch):
    from src.config import get_settings
    monkeypatch.setattr(get_settings(), "environment", "test")
    started = {"n": 0}
    monkeypatch.setattr(main, "start_scheduler", lambda **k: started.__setitem__("n", 1))
    monkeypatch.setattr(main, "shutdown_scheduler", lambda: None)
    async with main.lifespan(main.app):
        pass
    assert started["n"] == 0  # test env -> scheduler not started


@pytest.mark.asyncio
async def test_lifespan_starts_scheduler_in_production(monkeypatch):
    from src.config import get_settings
    settings = get_settings()
    monkeypatch.setattr(settings, "environment", "production")
    monkeypatch.setattr(settings, "read_only", False)
    monkeypatch.setattr(settings, "bulk_refresh_min_hours", 0)
    captured = {}
    monkeypatch.setattr(main, "start_scheduler",
                        lambda refresh_hours: captured.__setitem__("hours", refresh_hours))
    monkeypatch.setattr(main, "shutdown_scheduler", lambda: None)
    async with main.lifespan(main.app):
        pass
    assert captured["hours"] == 1  # max(1, 0)


@pytest.mark.asyncio
async def test_images_get_immutable_cache_control(client):
    # The /images/ branch of the cache-headers middleware (line 101).
    resp = await client.get("/images/nope.jpg")
    assert "immutable" in resp.headers.get("cache-control", "")


@pytest.mark.asyncio
async def test_static_gets_cache_control(client):
    resp = await client.get("/static/app.css")
    assert "max-age=86400" in resp.headers.get("cache-control", "")


def test_create_app_installs_lan_guard(monkeypatch):
    from src.config import get_settings
    monkeypatch.setattr(get_settings(), "lan_guard", True)
    app = main.create_app()  # covers the `if settings.lan_guard` install branch (line 118)
    assert app.title == "scryme"


async def _drive(app, decision, host="10.0.0.5", query=""):
    transport = ASGITransport(app=app, client=(host, 1234))
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        return await c.get(f"/health{query}")


@pytest.mark.asyncio
async def test_lan_guard_decisions(monkeypatch):
    from src.config import get_settings
    monkeypatch.setattr(get_settings(), "lan_guard", True)
    app = main.create_app()

    decision = {"value": "allow"}
    monkeypatch.setattr(main, "access_decision", lambda **kwargs: decision["value"])

    decision["value"] = "deny"
    r = await _drive(app, "deny")
    assert r.status_code == 403 and "turned off" in r.text

    decision["value"] = "unlock"
    r = await _drive(app, "unlock")
    assert r.status_code == 401  # _lan_unlock.html template

    decision["value"] = "allow"
    r = await _drive(app, "allow")
    assert r.status_code == 200

    decision["value"] = "set_cookie"
    r = await _drive(app, "set_cookie", query="?code=abc")
    assert r.status_code == 200 and "scryme_lan=abc" in r.headers.get("set-cookie", "")
