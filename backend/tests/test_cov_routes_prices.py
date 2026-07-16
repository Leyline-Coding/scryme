"""Coverage tests for src/routes/prices.py: the /prices page renders end to end."""

import uuid

import pytest
from src.models import Card, CollectionCard
from src.prices import snapshot_prices
from src.scryfall.mapping import card_to_columns


async def _seed(session):
    raw = {"id": str(uuid.uuid4()), "oracle_id": str(uuid.uuid4()), "name": "Aaa", "set": "TST",
           "collector_number": "1", "rarity": "rare", "prices": {"usd": "5.00"}}
    c = Card(**card_to_columns(raw))
    session.add(c)
    await session.flush()
    session.add(CollectionCard(scryfall_id=c.scryfall_id, quantity=2, finish="normal",
                               purchase_price=1.00))
    await session.commit()
    return c


@pytest.mark.asyncio
async def test_prices_page_renders(client, session):
    await _seed(session)
    await snapshot_prices(session)
    resp = await client.get("/prices")
    assert resp.status_code == 200
    assert "Collection value" in resp.text


@pytest.mark.asyncio
async def test_prices_page_renders_when_empty(client, session):
    # No collection, no snapshots -> the route still returns a 200 page.
    resp = await client.get("/prices")
    assert resp.status_code == 200
