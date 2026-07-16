"""Coverage for src/routes/admin.py — handlers called directly (HTTP bodies run in a greenlet
context the default coverage config does not trace)."""

import uuid

import pytest
import src.routes.admin as admin
from src.main import app
from src.models import Card, CollectionCard
from src.scryfall.mapping import card_to_columns
from starlette.requests import Request


def _request(path="/admin") -> Request:
    return Request({
        "type": "http", "method": "GET", "path": path, "raw_path": path.encode(),
        "query_string": b"", "headers": [], "app": app, "router": app.router,
        "scheme": "http", "server": ("test", 80), "client": ("test", 12345),
    })


async def _seed(session):
    raw = {"id": str(uuid.uuid4()), "oracle_id": str(uuid.uuid4()), "name": "Aaa", "set": "tst",
           "collector_number": "1", "rarity": "rare", "prices": {"usd": "1.00"}}
    c = Card(**card_to_columns(raw))
    c.image_status = "cached"
    session.add(c)
    await session.flush()
    session.add(CollectionCard(scryfall_id=c.scryfall_id, quantity=3))
    await session.commit()


@pytest.mark.asyncio
async def test_dashboard_renders(session):
    await _seed(session)
    resp = await admin.dashboard(_request(), session=session)
    assert resp.status_code == 200
    assert b"Card database" in resp.body


@pytest.mark.asyncio
async def test_metrics(session):
    await _seed(session)
    resp = await admin.metrics(session=session)
    assert resp.status_code == 200
    assert b"scryme_cards_total" in resp.body


@pytest.mark.asyncio
async def test_status(session):
    await _seed(session)
    body = await admin.status()
    assert body["card_count"] == 1 and body["ingest"]["status"] == "never"


@pytest.mark.asyncio
async def test_ingest_progress(session):
    out = await admin.ingest_progress()
    assert "card_count" in out and "phase" in out


@pytest.mark.asyncio
async def test_trigger_ingest_accepts(monkeypatch):
    from fastapi import BackgroundTasks

    called = {}

    async def fake_ingest(force=False):
        called["force"] = force

    monkeypatch.setattr(admin, "ingest_default_cards", fake_ingest)
    # ensure not read-only and no in-flight ingest
    from src.config import get_settings
    monkeypatch.setattr(get_settings(), "read_only", False)
    monkeypatch.setattr(admin, "get_ingest_progress", lambda: {"phase": "idle"})
    bg = BackgroundTasks()
    resp = await admin.trigger_ingest(bg, force=True)
    assert resp == {"accepted": True, "force": True}
    # run the queued task to cover _run_ingest -> ingest + evaluate_alerts
    import src.saved_alerts as sa
    monkeypatch.setattr(sa, "evaluate_alerts", lambda: _noop())
    await bg()
    assert called == {"force": True}


async def _noop():
    return None


@pytest.mark.asyncio
async def test_trigger_ingest_already_running(monkeypatch):
    from fastapi import BackgroundTasks
    from src.config import get_settings

    monkeypatch.setattr(get_settings(), "read_only", False)
    monkeypatch.setattr(admin, "get_ingest_progress", lambda: {"phase": "downloading"})
    resp = await admin.trigger_ingest(BackgroundTasks(), force=False)
    assert resp == {"accepted": False, "already_running": True}


@pytest.mark.asyncio
async def test_trigger_ingest_read_only(monkeypatch):
    from fastapi import BackgroundTasks, HTTPException
    from src.config import get_settings

    monkeypatch.setattr(get_settings(), "read_only", True)
    with pytest.raises(HTTPException) as exc:
        await admin.trigger_ingest(BackgroundTasks())
    assert exc.value.status_code == 403
