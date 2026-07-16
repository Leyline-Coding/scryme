"""Coverage for src/demo.py helpers + guard/edge branches not hit by the happy-path seed test."""

import datetime
import random
import uuid

import pytest
from sqlalchemy import func, select
from src.demo import (
    _collect_rows,
    _ensure_status,
    _seed_price_history,
    _take,
    seed_demo,
    seed_demo_decks,
)
from src.models import Card, CollectionCard, PriceSnapshot


async def _card(session, *, colors=None, usd=None, legalities=None, released=None):
    card = Card(
        scryfall_id=uuid.uuid4(),
        oracle_id=uuid.uuid4(),
        name=f"Demo {uuid.uuid4().hex[:6]}",
        set_code="tst",
        collector_number="1",
        color_identity=colors or [],
        prices={"usd": usd} if usd else {},
        legalities=legalities or {},
        released_at=released,
        raw={"name": "Demo"},
    )
    session.add(card)
    await session.flush()
    return card


def test_collect_rows_dedupes_and_stops_at_limit():
    used: set = set()
    out: list = []
    rows = [
        ("s1", "o1", "1.0"),
        ("s2", "o1", "2.0"),   # duplicate oracle -> skipped
        ("s3", "o3", None),    # priceless -> 0.0
        ("s4", "o4", "5.0"),   # never reached: limit hit first
    ]
    added = _collect_rows(rows, 2, used, out)
    assert added == 2
    assert out == [("s1", 1.0), ("s3", 0.0)]


@pytest.mark.asyncio
async def test_take_zero_count_is_noop(session):
    out: list = []
    await _take(session, Card.color_identity == ["R"], 0, set(), out)
    assert out == []


@pytest.mark.asyncio
async def test_take_small_count_fills_from_below_split_band(session):
    # count=1 -> "above $5" band wants 0 (need<=0 -> continue), below-split band fills it.
    await _card(session, colors=["R"], usd="2.00", released=datetime.date(2010, 1, 1))
    out: list = []
    await _take(session, Card.color_identity == ["R"], 1, set(), out)
    assert len(out) == 1


@pytest.mark.asyncio
async def test_ensure_status_adds_and_counts_existing(session):
    banned = await _card(session, colors=["B"], usd="4.00",
                         legalities={"modern": "banned"}, released=datetime.date(2015, 1, 1))
    # Fresh `used` -> the card is added via the else branch.
    out: list = []
    await _ensure_status(session, "modern", "banned", 1, set(), out)
    assert len(out) == 1

    # Pre-owned key -> counts toward the guarantee without re-adding.
    out2: list = []
    await _ensure_status(session, "modern", "banned", 1, {banned.oracle_id}, out2)
    assert out2 == []


@pytest.mark.asyncio
async def test_seed_price_history_skips_priceless_cards(session):
    priced = await _card(session, colors=["R"], usd="10.00")
    priceless = await _card(session, colors=["G"], usd=None)
    when = datetime.datetime(2020, 1, 1, tzinfo=datetime.UTC)
    for c in (priced, priceless):
        session.add(CollectionCard(scryfall_id=c.scryfall_id, quantity=1,
                                   source_format="demo", added_at=when))
    await session.commit()

    await _seed_price_history(session, random.Random(1))
    # Monthly snapshots were created (many months from 2005 to now).
    snaps = await session.scalar(select(func.count()).select_from(PriceSnapshot))
    assert snaps > 12


@pytest.mark.asyncio
async def test_seed_demo_skips_when_guarded(session, monkeypatch):
    import src.demo as demo
    monkeypatch.setattr(demo, "_SEED_GUARD", 1)
    card = await _card(session, colors=["R"], usd="1.00")
    session.add(CollectionCard(scryfall_id=card.scryfall_id, quantity=1, source_format="demo"))
    await session.commit()
    # Already at/above the (patched) guard -> skip without adding.
    assert await seed_demo() == 0


@pytest.mark.asyncio
async def test_seed_demo_decks_creates_then_skips(session):
    first = await seed_demo_decks()
    assert first == 3  # heavenly_inferno / elves / goblins
    second = await seed_demo_decks()
    assert second == 0  # all already exist -> skipped
