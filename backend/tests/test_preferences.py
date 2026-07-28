"""Preferences singleton (#203): service defaults/upsert/normalize + the /prefs & /api/v1 routes."""

import pytest
from src.config import get_settings
from src.preferences import get_preferences, update_preferences


@pytest.mark.asyncio
async def test_get_preferences_defaults_when_no_row(session):
    """No row -> env/built-in defaults, never writes a row."""
    prefs = await get_preferences(session)
    s = get_settings()
    assert prefs.currency == s.default_currency and prefs.price_source == s.default_price_source
    assert prefs.mode == "dark" and prefs.palette == "trop-orange" and prefs.view == "grid"
    assert prefs.page_size == 60 and prefs.movers is False and prefs.spin is True
    # get is read-only: still no row afterwards.
    from src.models import Preferences
    assert await session.get(Preferences, 1) is None


@pytest.mark.asyncio
async def test_update_preferences_upserts_and_normalizes(session):
    # First update creates id==1 and applies only the given fields.
    p = await update_preferences(session, currency="eur", palette="midnight", foil_speed=20)
    assert p.currency == "eur" and p.palette == "midnight"
    assert p.foil_speed == 10  # clamped to 1..10
    assert p.price_source == "tcgplayer"  # untouched -> default
    # A bad currency falls back to the default rather than persisting garbage.
    p2 = await update_preferences(session, currency="zzz", movers=True, extra={"beta": 1})
    assert p2.currency == get_settings().default_currency
    assert p2.movers is True and p2.extra == {"beta": 1}
    assert p2.palette == "midnight"  # earlier value persisted across updates
    # A second row is never created.
    from sqlalchemy import func, select
    from src.models import Preferences
    assert (await session.execute(select(func.count()).select_from(Preferences))).scalar() == 1


@pytest.mark.asyncio
async def test_prefs_route_get_and_patch(client):
    assert (await client.get("/prefs")).json()["currency"] == get_settings().default_currency
    r = await client.patch("/prefs", json={"mode": "light", "accent": "#ff8800"})
    assert r.status_code == 200
    body = r.json()
    assert body["mode"] == "light" and body["accent"] == "#ff8800"
    # exclude_unset: fields not sent keep their defaults/prior values.
    assert body["palette"] == "trop-orange"
    assert (await client.get("/prefs")).json()["mode"] == "light"


@pytest.mark.asyncio
async def test_prefs_patch_blocked_read_only(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "read_only", True)
    assert (await client.patch("/prefs", json={"mode": "light"})).status_code == 403
    assert (await client.get("/prefs")).status_code == 200  # reads still fine


@pytest.mark.asyncio
async def test_prefs_router_is_not_token_gated(client, monkeypatch):
    """The browser /prefs router must work even when SCRYME_API_TOKEN is set (unlike /api/v1)."""
    monkeypatch.setattr(get_settings(), "api_token", "secret")
    assert (await client.get("/prefs")).status_code == 200            # no token needed
    assert (await client.get("/api/v1/preferences")).status_code == 401  # token-gated
    ok = await client.get("/api/v1/preferences", headers={"Authorization": "Bearer secret"})
    assert ok.status_code == 200 and ok.json()["currency"] == get_settings().default_currency
