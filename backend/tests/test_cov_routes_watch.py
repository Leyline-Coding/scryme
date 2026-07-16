"""Coverage tests for src/routes/watch.py: add/delete/alerts + the read-only guard."""

import uuid

import pytest
from src.config import get_settings
from src.models import Card
from src.price_watch import evaluate_targets, list_targets


async def _card(session, usd="2.00"):
    card = Card(scryfall_id=uuid.uuid4(), name="Watched", set_code="tst",
                collector_number="1", prices={"usd": usd}, raw={"name": "Watched"})
    session.add(card)
    await session.commit()
    return card


@pytest.mark.asyncio
async def test_watch_add_and_delete(client, session):
    card = await _card(session)
    resp = await client.post(
        "/watch/add",
        data={"scryfall_id": str(card.scryfall_id), "direction": "below", "threshold": "5"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == f"/card/{card.scryfall_id}"

    rows = await list_targets(session)
    assert len(rows) == 1
    resp = await client.post(f"/watch/{rows[0].id}/delete", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/prices"
    assert await list_targets(session) == []


@pytest.mark.asyncio
async def test_alerts_summary(client, session):
    card = await _card(session)
    await client.post(
        "/watch/add",
        data={"scryfall_id": str(card.scryfall_id), "direction": "below", "threshold": "5"},
        follow_redirects=False,
    )
    await evaluate_targets(session)
    summary = (await client.get("/alerts")).json()
    assert summary["price"] == 1
    assert summary["total"] == summary["saved"] + summary["price"]


@pytest.mark.asyncio
async def test_read_only_blocks_add_and_delete(client, session, monkeypatch):
    card = await _card(session)
    monkeypatch.setattr(get_settings(), "read_only", True)
    resp = await client.post(
        "/watch/add",
        data={"scryfall_id": str(card.scryfall_id), "direction": "below", "threshold": "5"},
    )
    assert resp.status_code == 403
    resp = await client.post("/watch/1/delete")
    assert resp.status_code == 403
