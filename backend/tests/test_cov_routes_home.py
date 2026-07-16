"""Coverage for src/routes/home.py — the index handler's three states + read-only branch."""

import uuid

import pytest
import src.routes.home as home
from src.main import app
from src.models import Card, CollectionCard
from starlette.requests import Request


def _request() -> Request:
    return Request({
        "type": "http", "method": "GET", "path": "/", "raw_path": b"/", "query_string": b"",
        "headers": [], "app": app, "router": app.router, "scheme": "http",
        "server": ("test", 80), "client": ("test", 12345),
    })


def _card(session, owned=0):
    c = Card(scryfall_id=uuid.uuid4(), name="Black Lotus", set_code="lea",
             collector_number="232", raw={"name": "Black Lotus"}, prices={"usd": "1.00"})
    session.add(c)
    return c


@pytest.mark.asyncio
async def test_index_first_run_when_no_cards(session, monkeypatch):
    from src.config import get_settings
    monkeypatch.setattr(get_settings(), "read_only", False)
    resp = await home.index(_request(), session=session)
    assert resp.status_code == 200
    assert b"Setting up scryme" in resp.body  # needs_cards true


@pytest.mark.asyncio
async def test_index_upload_prompt_when_cards_but_empty_collection(session, monkeypatch):
    from src.config import get_settings
    monkeypatch.setattr(get_settings(), "read_only", False)
    _card(session)
    await session.commit()
    resp = await home.index(_request(), session=session)
    assert b"Upload a collection" in resp.body


@pytest.mark.asyncio
async def test_index_search_with_collection(session, monkeypatch):
    from src.config import get_settings
    monkeypatch.setattr(get_settings(), "read_only", False)
    c = _card(session)
    await session.flush()
    session.add(CollectionCard(scryfall_id=c.scryfall_id, quantity=1))
    await session.commit()
    resp = await home.index(_request(), session=session)
    assert b'name="q"' in resp.body  # digest + search path (has_collection true)


@pytest.mark.asyncio
async def test_index_read_only_skips_alerts(session, monkeypatch):
    from src.config import get_settings
    monkeypatch.setattr(get_settings(), "read_only", True)
    c = _card(session)
    await session.flush()
    session.add(CollectionCard(scryfall_id=c.scryfall_id, quantity=1))
    await session.commit()
    resp = await home.index(_request(), session=session)
    assert resp.status_code == 200  # alerts/price_alerts short-circuited to []
