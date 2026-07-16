"""Coverage for src/stats.py — the 'Other' primary-type bucket and growth aggregation."""

import datetime
import uuid

import pytest
from src.models import Card, CollectionCard
from src.scryfall.mapping import card_to_columns
from src.stats import (
    CollectionGrowth,
    CollectionStats,
    _color_bucket,
    _primary_type,
    collection_growth,
    collection_stats,
)


def test_primary_type_other():
    # A type line matching none of the known primary types -> "Other" (stats.py line 73).
    assert _primary_type("Dungeon") == "Other"
    assert _primary_type(None) == "Other"
    assert _primary_type("Legendary Creature — Elf") == "Creature"


def test_color_bucket_variants():
    assert _color_bucket(None) == "Colorless"
    assert _color_bucket(["W"]) == "White"          # single known color (line 63)
    assert _color_bucket(["Q"]) == "Q"              # unknown single color -> passthrough
    assert _color_bucket(["W", "U"]) == "Multicolor"


def test_dataclass_properties():
    assert CollectionStats().is_empty is True
    assert CollectionStats(total_cards=1).is_empty is False
    assert CollectionGrowth().available is False


@pytest.mark.asyncio
async def test_collection_stats_other_bucket(session):
    raw = {"id": str(uuid.uuid4()), "oracle_id": str(uuid.uuid4()), "name": "The Lost Mine",
           "set": "TST", "collector_number": "1", "rarity": "rare", "cmc": 0,
           "type_line": "Dungeon", "color_identity": ["W"], "prices": {"usd": "4.00"}}
    c = Card(**card_to_columns(raw))
    session.add(c)
    await session.flush()
    session.add(CollectionCard(scryfall_id=c.scryfall_id, quantity=1, finish="normal"))
    await session.commit()

    s = await collection_stats(session)
    assert s.total_cards == 1 and s.is_empty is False
    assert any(b.label == "Other" for b in s.by_type)
    assert any(b.label == "White" for b in s.by_color)
    # A priced card populates the "most valuable" list (the valued-card branch).
    assert s.most_valuable and s.most_valuable[0].usd == 4.00


@pytest.mark.asyncio
async def test_collection_growth_windowing(session):
    raw = {"id": str(uuid.uuid4()), "oracle_id": str(uuid.uuid4()), "name": "Aaa", "set": "TST",
           "collector_number": "1", "rarity": "rare", "prices": {"usd": "2.00"}}
    c = Card(**card_to_columns(raw))
    session.add(c)
    await session.flush()
    jan = datetime.datetime(2026, 1, 10, tzinfo=datetime.UTC)
    mar = datetime.datetime(2026, 3, 5, tzinfo=datetime.UTC)
    session.add(CollectionCard(scryfall_id=c.scryfall_id, quantity=3, finish="normal",
                               added_at=jan))
    session.add(CollectionCard(scryfall_id=c.scryfall_id, quantity=2, finish="normal",
                               added_at=mar))
    await session.commit()

    # months=1 -> only the most recent active month is shown, but totals cover all history.
    g = await collection_growth(session, months=1)
    assert [p.label for p in g.points] == ["2026-03"]
    assert g.total_added == 5 and g.total_months == 2 and g.windowed is True
    assert g.max_added == 2
