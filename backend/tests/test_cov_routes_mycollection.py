"""Coverage tests for src/routes/mycollection.py: every /collection tab renders."""

import uuid

import pytest
from src.models import Card, CollectionCard
from src.scryfall.mapping import card_to_columns


async def _own(session, *, tags=None):
    c = Card(**card_to_columns(
        {"id": str(uuid.uuid4()), "oracle_id": str(uuid.uuid4()), "name": "Owned",
         "set": "tst", "collector_number": "1", "rarity": "rare", "prices": {"usd": "5.00"}}))
    session.add(c)
    await session.flush()
    session.add(CollectionCard(scryfall_id=c.scryfall_id, quantity=2, tags=tags))
    await session.commit()
    return c


@pytest.mark.parametrize("tab", [
    "stats", "locations", "binders", "decks", "tags",
    "wishlist", "checklists", "trade", "sell",
])
@pytest.mark.asyncio
async def test_each_tab_renders(client, session, tab):
    await _own(session, tags=["for-sale"] if tab in ("sell", "trade", "tags") else None)
    resp = await client.get(f"/collection?tab={tab}")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_unknown_tab_falls_back_to_stats(client, session):
    resp = await client.get("/collection?tab=bogus")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_stats_sets_view(client, session):
    await _own(session)
    resp = await client.get("/collection?tab=stats&view=sets")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_stats_with_price_history(client, session):
    from src.models import PriceSnapshot
    await _own(session)
    session.add(PriceSnapshot(total_usd=5.0, card_count=1))
    await session.commit()
    resp = await client.get("/collection?tab=stats")
    assert resp.status_code == 200
