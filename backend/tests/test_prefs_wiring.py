"""Preferences wiring (#203): resolve() precedence, the read helpers reading request.state.prefs,
and the template context processor."""

import pytest
from src.config import get_settings
from src.currency import get_currency
from src.models import Preferences
from src.preferences import Prefs, resolve, update_preferences
from src.pricing import get_price_source
from src.templating import _inject_prefs
from starlette.requests import Request


def _req(cookie: str = "", prefs=None) -> Request:
    headers = [(b"cookie", cookie.encode())] if cookie else []
    scope = {"type": "http", "method": "GET", "path": "/", "headers": headers, "state": {},
             "query_string": b""}
    req = Request(scope)
    if prefs is not None:
        req.state.prefs = prefs
    return req


@pytest.mark.asyncio
async def test_resolve_precedence(session):
    default_cur = get_settings().default_currency
    # No row: defaults, and a device cookie still applies (backward compat before anything saved).
    assert resolve(None, {}, writable=True).currency == default_cur
    assert resolve(None, {"scryme_currency": "eur"}, writable=True).currency == "eur"

    await update_preferences(session, currency="jpy")
    row = await session.get(Preferences, 1)
    # Writable + saved row: the singleton is authoritative (ignores the device cookie).
    assert resolve(row, {"scryme_currency": "eur"}, writable=True).currency == "jpy"
    # Read-only demo: the per-device cookie wins, else the singleton value.
    assert resolve(row, {"scryme_currency": "eur"}, writable=False).currency == "eur"
    assert resolve(row, {}, writable=False).currency == "jpy"
    # Cookie-only prefs merge too (movers/view), appearance stays from the row.
    merged = resolve(row, {"scryme_view": "list", "scryme_movers": "1"}, writable=False)
    assert merged.view == "list" and merged.movers is True and merged.palette == "trop-orange"


def test_read_helpers_prefer_prefs():
    # With resolved prefs on the request, the helpers use them (no cookie needed).
    req = _req(prefs=Prefs(currency="gbp", price_source="cardkingdom"))
    assert get_currency(req) == "gbp" and get_price_source(req) == "cardkingdom"
    # Without prefs (middleware didn't run), they fall back to the cookie, then the default.
    assert get_currency(_req(cookie="scryme_currency=eur")) == "eur"
    assert get_price_source(_req(cookie="scryme_price_source=manapool")) == "manapool"
    assert get_currency(_req()) == get_settings().default_currency


def test_context_processor_injects_appearance():
    got = _inject_prefs(_req(prefs=Prefs(palette="midnight", mode="light")))
    assert got["prefs_writable"] is True
    assert got["theme_prefs"]["palette"] == "midnight" and got["theme_prefs"]["mode"] == "light"
    # No prefs on the request -> empty, not writable (base.html falls back to localStorage).
    empty = _inject_prefs(_req())
    assert empty["theme_prefs"] is None and empty["prefs_writable"] is False


@pytest.mark.asyncio
async def test_middleware_wires_prefs_end_to_end(client):
    # Save a preference; a normal page render must not 500 (middleware ran) and the value holds.
    assert (await client.patch("/prefs", json={"currency": "eur"})).status_code == 200
    assert (await client.get("/")).status_code == 200
    assert (await client.get("/prefs")).json()["currency"] == "eur"
