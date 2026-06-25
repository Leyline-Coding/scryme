"""Merge strategy tests: aggregate, conflicts, replace / increment / per-card."""

import pytest
from sqlalchemy import func, select
from src.importers.base import ImportRow
from src.importers.matching import MatchedRow
from src.importers.merge import (
    MergeStrategy,
    aggregate,
    apply_merge,
)
from src.models import CollectionCard

from tests.seed_cards import BLACK_LOTUS, LIGHTNING_BOLT, seed_cards


def _matched(sid, name, qty, finish="normal"):
    row = ImportRow(name=name, scryfall_id=sid, quantity=qty, finish=finish)
    return MatchedRow(row, sid, "scryfall_id")


async def _qty(session, sid) -> int:
    return await session.scalar(
        select(func.coalesce(func.sum(CollectionCard.quantity), 0)).where(
            CollectionCard.scryfall_id == sid
        )
    )


def test_aggregate_sums_identical_stacks():
    stacks = aggregate([_matched(BLACK_LOTUS, "Black Lotus", 1),
                        _matched(BLACK_LOTUS, "Black Lotus", 2)])
    assert len(stacks) == 1
    assert next(iter(stacks.values())).quantity == 3


def test_aggregate_skips_unmatched():
    stacks = aggregate([MatchedRow(ImportRow(name="x"), None, "unmatched")])
    assert stacks == {}


@pytest.mark.asyncio
async def test_increment_into_empty(session):
    await seed_cards(session)
    summary = await apply_merge(
        session, [_matched(BLACK_LOTUS, "Black Lotus", 2)], MergeStrategy.INCREMENT
    )
    assert summary.inserted == 1
    assert await _qty(session, BLACK_LOTUS) == 2


@pytest.mark.asyncio
async def test_increment_existing_sums(session):
    await seed_cards(session)
    session.add(CollectionCard(scryfall_id=BLACK_LOTUS, quantity=1))
    await session.commit()

    summary = await apply_merge(
        session, [_matched(BLACK_LOTUS, "Black Lotus", 2)], MergeStrategy.INCREMENT
    )
    assert summary.updated == 1
    assert await _qty(session, BLACK_LOTUS) == 3


@pytest.mark.asyncio
async def test_replace_wipes_then_inserts(session):
    await seed_cards(session)
    session.add(CollectionCard(scryfall_id=BLACK_LOTUS, quantity=5))
    await session.commit()

    await apply_merge(
        session, [_matched(LIGHTNING_BOLT, "Lightning Bolt", 1)], MergeStrategy.REPLACE
    )
    assert await _qty(session, BLACK_LOTUS) == 0  # wiped
    assert await _qty(session, LIGHTNING_BOLT) == 1


@pytest.mark.asyncio
async def test_per_card_decisions(session):
    await seed_cards(session)
    session.add(CollectionCard(scryfall_id=BLACK_LOTUS, quantity=1))
    session.add(CollectionCard(scryfall_id=LIGHTNING_BOLT, quantity=1))
    await session.commit()

    matched = [
        _matched(BLACK_LOTUS, "Black Lotus", 2),
        _matched(LIGHTNING_BOLT, "Lightning Bolt", 3),
    ]
    # Conflicts sorted by name: Black Lotus -> 0, Lightning Bolt -> 1.
    await apply_merge(
        session, matched, MergeStrategy.PER_CARD, decisions={0: "replace", 1: "increment"}
    )
    assert await _qty(session, BLACK_LOTUS) == 2  # replaced
    assert await _qty(session, LIGHTNING_BOLT) == 4  # incremented (1 + 3)
