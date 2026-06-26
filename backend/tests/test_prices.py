"""Price history tests: snapshotting, value series, movers, and the route."""

import uuid

import pytest
from src.models import Card, CollectionCard
from src.prices import biggest_movers, snapshot_prices, value_series
from src.scryfall.mapping import card_to_columns


async def _seed(session):
    a = {"id": str(uuid.uuid4()), "oracle_id": str(uuid.uuid4()), "name": "Aaa", "set": "TST",
         "collector_number": "1", "rarity": "common", "cmc": 1, "type_line": "Creature",
         "colors": ["W"], "color_identity": ["W"], "prices": {"usd": "1.00"}}
    b = {"id": str(uuid.uuid4()), "oracle_id": str(uuid.uuid4()), "name": "Bbb", "set": "TST",
         "collector_number": "2", "rarity": "rare", "cmc": 3, "type_line": "Instant",
         "colors": ["U"], "color_identity": ["U"], "prices": {"usd": "5.00", "usd_foil": "12.00"}}
    ca, cb = Card(**card_to_columns(a)), Card(**card_to_columns(b))
    session.add_all([ca, cb])
    await session.flush()
    session.add(CollectionCard(scryfall_id=ca.scryfall_id, quantity=2, finish="normal"))
    session.add(CollectionCard(scryfall_id=cb.scryfall_id, quantity=1, finish="foil"))
    await session.commit()
    return ca, cb


@pytest.mark.asyncio
async def test_snapshot_value_is_foil_aware(session):
    await _seed(session)
    snap = await snapshot_prices(session)
    # 2 * 1.00 (normal) + 1 * 12.00 (foil) = 14.00
    assert snap.total_usd == 14.00
    assert snap.card_count == 2  # both have a market USD


@pytest.mark.asyncio
async def test_empty_collection_snapshot_is_none(session):
    assert await snapshot_prices(session) is None


@pytest.mark.asyncio
async def test_value_series_is_chronological(session):
    await _seed(session)
    await snapshot_prices(session)
    await snapshot_prices(session)
    series = await value_series(session)
    assert len(series) == 2
    assert series[0].captured_at <= series[1].captured_at


@pytest.mark.asyncio
async def test_biggest_movers(session):
    ca, _ = await _seed(session)
    await snapshot_prices(session)
    # Aaa's market price rises 1.00 -> 3.00; re-snapshot.
    ca.raw = {**ca.raw, "prices": {"usd": "3.00"}}
    ca.prices = {"usd": "3.00"}
    await session.commit()
    await snapshot_prices(session)

    movers = await biggest_movers(session)
    assert movers.available
    assert [m.name for m in movers.gainers] == ["Aaa"]
    top = movers.gainers[0]
    assert top.old == 1.00 and top.new == 3.00 and top.delta == 2.00 and top.pct == 200.0
    assert movers.losers == []


@pytest.mark.asyncio
async def test_prices_route_renders(client, session):
    await _seed(session)
    await snapshot_prices(session)
    resp = await client.get("/prices")
    assert resp.status_code == 200
    assert "Collection value" in resp.text
    assert "$14.00" in resp.text
