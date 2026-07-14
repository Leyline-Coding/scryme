"""Price history tests: snapshotting, value series, movers, and the route."""

import datetime
import uuid
from types import SimpleNamespace

import pytest
from src.models import Card, CollectionCard
from src.prices import (
    biggest_movers,
    build_value_chart,
    collection_pl,
    snapshot_prices,
    value_series,
)
from src.scryfall.mapping import card_to_columns


def _snap(usd, day):
    return SimpleNamespace(total_usd=usd, captured_at=datetime.datetime(2026, 6, day))


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


async def _seed_pl(session):
    """A winner (A), a loser (B, foil), and an unpriced stack (C, no purchase price)."""
    a = {"id": str(uuid.uuid4()), "oracle_id": str(uuid.uuid4()), "name": "Aaa", "set": "TST",
         "collector_number": "1", "rarity": "common", "prices": {"usd": "1.00"}}
    b = {"id": str(uuid.uuid4()), "oracle_id": str(uuid.uuid4()), "name": "Bbb", "set": "TST",
         "collector_number": "2", "rarity": "rare", "prices": {"usd": "5.00", "usd_foil": "12.00"}}
    c = {"id": str(uuid.uuid4()), "oracle_id": str(uuid.uuid4()), "name": "Ccc", "set": "TST",
         "collector_number": "3", "rarity": "common", "prices": {"usd": "2.00"}}
    ca, cb, cc = (Card(**card_to_columns(x)) for x in (a, b, c))
    session.add_all([ca, cb, cc])
    await session.flush()
    session.add(CollectionCard(scryfall_id=ca.scryfall_id, quantity=2, finish="normal",
                               purchase_price=0.50))
    session.add(CollectionCard(scryfall_id=cb.scryfall_id, quantity=1, finish="foil",
                               purchase_price=20.00))
    session.add(CollectionCard(scryfall_id=cc.scryfall_id, quantity=1, finish="normal"))
    await session.commit()


@pytest.mark.asyncio
async def test_collection_pl_totals_and_movers(session):
    await _seed_pl(session)
    pl = await collection_pl(session)
    assert pl.available
    # cost: 2*0.50 + 1*20.00 = 21.00 ; market (foil-aware): 2*1.00 + 1*12.00 = 14.00
    assert pl.cost_basis == 21.00
    assert pl.market_value == 14.00
    assert pl.unrealized == -7.00
    assert pl.pct == -33.3
    assert pl.priced_stacks == 2
    assert pl.priced_cards == 3
    assert pl.unpriced_stacks == 1  # Ccc has no purchase price
    assert [w.name for w in pl.winners] == ["Aaa"]
    assert pl.winners[0].total_delta == 1.00  # 2 * (1.00 - 0.50)
    assert [loser.name for loser in pl.losers] == ["Bbb"]
    assert pl.losers[0].total_delta == -8.00  # 1 * (12.00 - 20.00), foil price


@pytest.mark.asyncio
async def test_collection_pl_empty_when_no_purchase_prices(session):
    await _seed(session)  # _seed sets no purchase prices
    pl = await collection_pl(session)
    assert not pl.available
    assert pl.cost_basis == 0.0
    assert pl.unpriced_stacks == 2


@pytest.mark.asyncio
async def test_prices_route_renders(client, session):
    await _seed(session)
    await snapshot_prices(session)
    resp = await client.get("/prices")
    assert resp.status_code == 200
    assert "Collection value" in resp.text
    assert "$14.00" in resp.text


@pytest.mark.asyncio
async def test_prices_route_shows_pl(client, session):
    await _seed_pl(session)
    resp = await client.get("/prices")
    assert resp.status_code == 200
    assert "Acquisition P/L" in resp.text
    assert "Cost basis" in resp.text


def test_value_chart_empty():
    chart = build_value_chart([])
    assert not chart.available
    assert not chart.has_trend
    assert chart.points == []


def test_value_chart_single_point():
    chart = build_value_chart([_snap(100.0, 1)])
    assert chart.available and not chart.has_trend
    assert len(chart.points) == 1
    assert chart.current == 100.0
    assert chart.first_date == chart.last_date == "2026-06-01"


def test_value_chart_trend_maps_extremes_to_edges():
    chart = build_value_chart(
        [_snap(100.0, 1), _snap(50.0, 2), _snap(150.0, 3)], width=1000, height=140, pad=8
    )
    assert chart.has_trend
    assert chart.min_value == 50.0 and chart.max_value == 150.0 and chart.current == 150.0
    # x spans the padded width, evenly spaced.
    assert chart.points[0].x == 8.0 and chart.points[-1].x == 992.0
    # Higher value -> nearer the top (smaller y); lower value -> nearer the bottom.
    ys = {p.value: p.y for p in chart.points}
    assert ys[150.0] == 8.0          # top = pad
    assert ys[50.0] == 132.0         # bottom = height - pad
    assert chart.area.startswith("M 8.0,140") and chart.area.endswith("Z")


def test_value_chart_flat_series_sits_on_midline():
    chart = build_value_chart([_snap(10.0, 1), _snap(10.0, 2)], height=140, pad=8)
    # Equal values -> midline (pad + inner_h/2 = 8 + 124/2 = 70).
    assert {p.y for p in chart.points} == {70.0}


@pytest.mark.asyncio
async def test_stats_route_shows_value_chart(client, session):
    await _seed(session)
    await snapshot_prices(session)
    await snapshot_prices(session)
    resp = await client.get("/collection?tab=stats")
    assert resp.status_code == 200
    assert "Value over time" in resp.text
    assert "<polyline" in resp.text


@pytest.mark.asyncio
async def test_collection_digest_week_over_week(session):
    from datetime import timedelta

    from src.models import CardPricePoint, PriceSnapshot
    from src.prices import collection_digest
    ca, cb = await _seed(session)
    now = datetime.datetime(2026, 6, 15, tzinfo=datetime.UTC)
    old = PriceSnapshot(captured_at=now - timedelta(days=7), total_usd=100.0, card_count=2)
    new = PriceSnapshot(captured_at=now, total_usd=130.0, card_count=2)
    session.add_all([old, new])
    await session.flush()
    session.add_all([
        CardPricePoint(snapshot_id=old.id, scryfall_id=ca.scryfall_id, usd=5.0),
        CardPricePoint(snapshot_id=new.id, scryfall_id=ca.scryfall_id, usd=20.0),   # +15 gain
        CardPricePoint(snapshot_id=old.id, scryfall_id=cb.scryfall_id, usd=30.0),
        CardPricePoint(snapshot_id=new.id, scryfall_id=cb.scryfall_id, usd=10.0),   # -20 loss
    ])
    await session.commit()

    d = await collection_digest(session, days=7)
    assert d.available and d.start_value == 100.0 and d.end_value == 130.0
    assert d.delta == 30.0 and d.pct == 30.0
    assert [m.name for m in d.gainers] == ["Aaa"] and [m.name for m in d.losers] == ["Bbb"]


@pytest.mark.asyncio
async def test_collection_digest_needs_two_snapshots(session):
    from src.prices import collection_digest
    await _seed(session)
    await snapshot_prices(session)  # only one snapshot
    assert not (await collection_digest(session)).available
