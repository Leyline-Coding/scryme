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
    assert prefs.card_size == 5
    # get is read-only: still no row afterwards.
    from src.models import Preferences
    assert await session.get(Preferences, 1) is None


@pytest.mark.asyncio
async def test_update_preferences_upserts_and_normalizes(session):
    # First update creates id==1 and applies only the given fields.
    p = await update_preferences(session, currency="eur", palette="midnight", foil_speed=20,
                                 card_size=99)
    assert p.currency == "eur" and p.palette == "midnight"
    assert p.foil_speed == 10  # clamped to 1..10
    assert p.card_size == 10  # clamped to 1..10
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


# --- PATCH /api/v1/preferences (the programmatic twin of /prefs) ---------------------------------

@pytest.mark.asyncio
async def test_api_preferences_patch_updates_and_persists(client):
    r = await client.patch("/api/v1/preferences", json={"currency": "eur", "movers": True})
    assert r.status_code == 200
    body = r.json()
    assert body["currency"] == "eur" and body["movers"] is True
    # Same singleton as the browser router — a write here is visible there.
    assert (await client.get("/prefs")).json()["currency"] == "eur"


@pytest.mark.asyncio
async def test_api_preferences_patch_is_partial(client):
    await client.patch("/api/v1/preferences", json={"palette": "midnight", "card_size": 8})
    body = (await client.patch("/api/v1/preferences", json={"mode": "light"})).json()
    assert body["mode"] == "light"
    assert body["palette"] == "midnight" and body["card_size"] == 8  # exclude_unset


@pytest.mark.asyncio
async def test_api_preferences_patch_normalizes_bad_values(client):
    """Garbage never persists: unknown enums fall back, numbers clamp."""
    body = (await client.patch("/api/v1/preferences", json={
        "currency": "zzz", "price_source": "not-a-shop", "view": "sideways",
        "page_size": 9999, "card_size": 99, "foil_speed": 0, "spin_speed": 42,
    })).json()
    assert body["currency"] == get_settings().default_currency
    assert body["price_source"] == get_settings().default_price_source
    assert body["view"] == "grid"          # anything but "list"
    assert body["page_size"] == 500        # clamped to 1..500
    assert body["card_size"] == 10         # clamped to 1..10
    assert body["foil_speed"] == 1 and body["spin_speed"] == 10


@pytest.mark.asyncio
async def test_api_preferences_patch_accepts_a_real_price_source(client):
    body = (await client.patch("/api/v1/preferences",
                               json={"price_source": "cardkingdom"})).json()
    assert body["price_source"] == "cardkingdom"


@pytest.mark.asyncio
async def test_api_preferences_patch_explicit_null_is_ignored(client):
    """A null means "not set" and must not wipe a stored value."""
    await client.patch("/api/v1/preferences", json={"palette": "midnight"})
    body = (await client.patch("/api/v1/preferences", json={"palette": None})).json()
    assert body["palette"] == "midnight"


@pytest.mark.asyncio
async def test_api_preferences_patch_ignores_unknown_fields(client):
    body = (await client.patch("/api/v1/preferences",
                               json={"mode": "light", "not_a_pref": "x"})).json()
    assert body["mode"] == "light" and "not_a_pref" not in body


@pytest.mark.asyncio
async def test_api_preferences_patch_blocked_read_only(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "read_only", True)
    assert (await client.patch("/api/v1/preferences", json={"mode": "light"})).status_code == 403
    assert (await client.get("/api/v1/preferences")).status_code == 200  # reads still fine


@pytest.mark.asyncio
async def test_api_preferences_patch_is_token_gated(client, monkeypatch):
    """Unlike /prefs, the versioned router requires the token when one is configured."""
    monkeypatch.setattr(get_settings(), "api_token", "secret")
    assert (await client.patch("/api/v1/preferences",
                               json={"mode": "light"})).status_code == 401
    ok = await client.patch("/api/v1/preferences", json={"mode": "light"},
                            headers={"X-API-Key": "secret"})
    assert ok.status_code == 200 and ok.json()["mode"] == "light"


@pytest.mark.asyncio
async def test_update_preferences_clamp_falls_back_on_non_numeric(session):
    """Reachable from the CLI/service layer — the API's typed model rejects these first."""
    p = await update_preferences(session, page_size="not-a-number", foil_speed=None)
    assert p.page_size == 60   # default, not a crash
    assert p.foil_speed == 6   # None is skipped entirely


# --- cookie overlay (resolve): a device's own choices win until a row is saved -------------------

def test_resolve_applies_cookies_when_no_row_saved():
    from src.preferences import resolve

    prefs = resolve(None, {
        "scryme_currency": "eur", "scryme_price_source": "manapool",
        "scryme_search_filter": "t:creature", "scryme_movers": "1",
        "scryme_view": "list", "scryme_infinite": "1",
        "scryme_hist_currency": "gbp", "scryme_page_size": "120",
    }, writable=True)
    assert prefs.currency == "eur" and prefs.price_source == "manapool"
    assert prefs.search_filter == "t:creature" and prefs.movers is True
    assert prefs.view == "list" and prefs.infinite is True
    assert prefs.hist_currency == "gbp" and prefs.page_size == 120


def test_resolve_ignores_junk_and_off_menu_cookie_values():
    from src.preferences import resolve

    prefs = resolve(None, {
        "scryme_page_size": "999",   # not one of the offered sizes -> ignored
        "scryme_view": "carousel",   # anything but "list" -> grid
        "scryme_infinite": "0",
    }, writable=True)
    assert prefs.page_size == 60 and prefs.view == "grid" and prefs.infinite is False

    assert resolve(None, {"scryme_page_size": "abc"}, writable=True).page_size == 60
