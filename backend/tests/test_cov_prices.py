"""Coverage tests for src/prices.py: the branches the existing suite leaves uncovered."""

import datetime
import uuid
from types import SimpleNamespace

import pytest
from src.models import Card, CardPricePoint, CollectionCard, PriceSnapshot
from src.prices import (
    DEFAULT_RANGE,
    _f,
    biggest_movers,
    build_value_chart,
    collection_digest,
    collection_pl,
    range_days,
    snapshot_prices,
    take_snapshot,
    value_series,
)
from src.scryfall.mapping import card_to_columns


def _fake_snap(usd, day):
    return SimpleNamespace(total_usd=usd, captured_at=datetime.datetime(2026, 6, day))


async def _seed(session):
    a = {"id": str(uuid.uuid4()), "oracle_id": str(uuid.uuid4()), "name": "Aaa", "set": "TST",
         "collector_number": "1", "rarity": "common", "prices": {"usd": "1.00"}}
    b = {"id": str(uuid.uuid4()), "oracle_id": str(uuid.uuid4()), "name": "Bbb", "set": "TST",
         "collector_number": "2", "rarity": "rare", "prices": {"usd": "5.00", "usd_foil": "12.00"}}
    ca, cb = Card(**card_to_columns(a)), Card(**card_to_columns(b))
    session.add_all([ca, cb])
    await session.flush()
    session.add(CollectionCard(scryfall_id=ca.scryfall_id, quantity=2, finish="normal"))
    session.add(CollectionCard(scryfall_id=cb.scryfall_id, quantity=1, finish="foil"))
    await session.commit()
    return ca, cb


# --- small helpers -------------------------------------------------------------------------

def test_f_handles_bad_values():
    assert _f("abc") == 0.0        # ValueError -> 0.0
    assert _f(None) == 0.0         # falsy -> 0.0
    assert _f("2.5") == 2.5


def test_range_days():
    assert range_days("30d") == 30
    assert range_days("all") is None
    assert range_days("bogus") == range_days(DEFAULT_RANGE)   # unknown -> default


# --- snapshot / take_snapshot --------------------------------------------------------------

@pytest.mark.asyncio
async def test_snapshot_empty_collection_is_none(session):
    assert await snapshot_prices(session) is None


@pytest.mark.asyncio
async def test_take_snapshot_opens_own_session(session):
    await _seed(session)
    snap = await take_snapshot()   # opens its own SessionLocal
    assert snap is not None
    assert snap.total_usd == 14.00   # 2 * 1.00 + 1 * 12.00 (foil)


# --- value_series --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_value_series_downsamples_over_400_points(session):
    base = datetime.datetime(2020, 1, 1, tzinfo=datetime.UTC)
    snaps = [PriceSnapshot(total_usd=float(i), card_count=0,
                           captured_at=base + datetime.timedelta(days=i)) for i in range(402)]
    session.add_all(snaps)
    await session.commit()
    series = await value_series(session, days=None)
    assert len(series) == 401           # downsampled to 400 + the retained latest
    assert series[-1].total_usd == 401.0


@pytest.mark.asyncio
async def test_value_series_default_window_filters_old(session):
    await _seed(session)
    session.add(PriceSnapshot(total_usd=1.0, card_count=0,
                              captured_at=datetime.datetime(2000, 1, 1, tzinfo=datetime.UTC)))
    await session.commit()
    await take_snapshot()
    series = await value_series(session)   # default 90-day window
    assert all(s.captured_at.year > 2000 for s in series)


# --- build_value_chart ---------------------------------------------------------------------

def test_build_value_chart_empty():
    chart = build_value_chart([])
    assert not chart.available and not chart.has_trend
    assert chart.first_date == "" and chart.last_date == "" and chart.points == []


def test_build_value_chart_single_point():
    chart = build_value_chart([_fake_snap(100.0, 1)])
    assert chart.available and not chart.has_trend
    assert chart.first_date == chart.last_date == "2026-06-01"
    assert chart.current == 100.0


def test_build_value_chart_trend_and_flat():
    trend = build_value_chart([_fake_snap(100.0, 1), _fake_snap(50.0, 2), _fake_snap(150.0, 3)])
    assert trend.has_trend
    assert trend.min_value == 50.0 and trend.max_value == 150.0
    assert trend.polyline and trend.area.endswith("Z")
    flat = build_value_chart([_fake_snap(10.0, 1), _fake_snap(10.0, 2)], height=140, pad=8)
    assert {p.y for p in flat.points} == {70.0}   # equal values sit on the midline


# --- movers --------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_biggest_movers_needs_two_snapshots(session):
    await _seed(session)
    await take_snapshot()   # one snapshot only
    movers = await biggest_movers(session)
    assert not movers.available and movers.gainers == [] and movers.losers == []


@pytest.mark.asyncio
async def test_biggest_movers_no_change_returns_empty(session):
    await _seed(session)
    await take_snapshot()
    await take_snapshot()   # identical prices -> no shared movers
    movers = await biggest_movers(session)
    assert not movers.available


@pytest.mark.asyncio
async def test_biggest_movers_gainers_and_losers(session):
    ca, cb = await _seed(session)
    now = datetime.datetime(2026, 6, 15, tzinfo=datetime.UTC)
    old = PriceSnapshot(captured_at=now - datetime.timedelta(days=1), total_usd=100.0, card_count=2)
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

    movers = await biggest_movers(session)
    assert movers.available
    assert [m.name for m in movers.gainers] == ["Aaa"]
    assert [m.name for m in movers.losers] == ["Bbb"]
    # Mover.delta / Mover.pct properties
    g = movers.gainers[0]
    assert g.delta == 15.0 and g.pct == 300.0
    assert g.set_code == "TST"
    assert movers.losers[0].delta == -20.0


# --- collection_digest ---------------------------------------------------------------------

@pytest.mark.asyncio
async def test_collection_digest_no_snapshots(session):
    await _seed(session)
    digest = await collection_digest(session)
    assert not digest.available and digest.delta == 0.0 and digest.pct == 0.0


@pytest.mark.asyncio
async def test_collection_digest_single_snapshot_falls_back(session):
    # One snapshot: no window baseline -> fallback query -> still none -> unavailable Digest.
    await _seed(session)
    await take_snapshot()
    digest = await collection_digest(session, days=7)
    assert not digest.available


@pytest.mark.asyncio
async def test_collection_digest_window(session):
    ca, cb = await _seed(session)
    now = datetime.datetime(2026, 6, 15, tzinfo=datetime.UTC)
    old = PriceSnapshot(captured_at=now - datetime.timedelta(days=7), total_usd=100.0, card_count=2)
    new = PriceSnapshot(captured_at=now, total_usd=130.0, card_count=2)
    session.add_all([old, new])
    await session.flush()
    session.add_all([
        CardPricePoint(snapshot_id=old.id, scryfall_id=ca.scryfall_id, usd=5.0),
        CardPricePoint(snapshot_id=new.id, scryfall_id=ca.scryfall_id, usd=20.0),
        CardPricePoint(snapshot_id=old.id, scryfall_id=cb.scryfall_id, usd=30.0),
        CardPricePoint(snapshot_id=new.id, scryfall_id=cb.scryfall_id, usd=10.0),
    ])
    await session.commit()

    d = await collection_digest(session, days=7)
    assert d.available and d.start_value == 100.0 and d.end_value == 130.0
    assert d.delta == 30.0 and d.pct == 30.0
    assert [m.name for m in d.gainers] == ["Aaa"] and [m.name for m in d.losers] == ["Bbb"]


# --- collection_pl -------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_collection_pl_zero_qty_unpriced_and_unit_delta(session):
    winner = {"id": str(uuid.uuid4()), "oracle_id": str(uuid.uuid4()), "name": "Win", "set": "tst",
              "collector_number": "1", "rarity": "rare", "prices": {"usd": "5.00"}}
    zero = {"id": str(uuid.uuid4()), "oracle_id": str(uuid.uuid4()), "name": "Zero", "set": "tst",
            "collector_number": "2", "rarity": "rare", "prices": {"usd": "9.00"}}
    unpriced = {"id": str(uuid.uuid4()), "oracle_id": str(uuid.uuid4()), "name": "NoBuy",
                "set": "tst", "collector_number": "3", "rarity": "rare", "prices": {"usd": "2.00"}}
    cw, cz, cu = (Card(**card_to_columns(x)) for x in (winner, zero, unpriced))
    session.add_all([cw, cz, cu])
    await session.flush()
    session.add(CollectionCard(scryfall_id=cw.scryfall_id, quantity=3, finish="normal",
                               purchase_price=1.00))
    session.add(CollectionCard(scryfall_id=cz.scryfall_id, quantity=0, finish="normal",
                               purchase_price=1.00))   # zero qty -> skipped
    session.add(CollectionCard(scryfall_id=cu.scryfall_id, quantity=1, finish="normal"))  # no cost
    await session.commit()

    pl = await collection_pl(session)
    assert pl.priced_stacks == 1 and pl.priced_cards == 3
    assert pl.unpriced_stacks == 1        # the no-purchase-price stack
    assert [w.name for w in pl.winners] == ["Win"]
    assert pl.winners[0].unit_delta == 4.00     # market - cost per card
    assert pl.winners[0].total_delta == 12.00
