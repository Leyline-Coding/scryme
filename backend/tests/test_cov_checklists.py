"""Coverage tests for src.checklists: name parsing, creation, coverage, add-to-wishlist."""

import uuid

import pytest
from sqlalchemy import select
from src.checklists import (
    _distinct_names,
    add_checklist_missing,
    checklist_coverage,
    create_checklist,
)
from src.models import Card, CollectionCard, WishlistItem
from src.scryfall.mapping import card_to_columns


async def _card(session, name, n, owned=False):
    c = Card(**card_to_columns(
        {"id": str(uuid.uuid4()), "oracle_id": str(uuid.uuid4()), "name": name, "set": "tst",
         "collector_number": str(n), "rarity": "rare", "prices": {"usd": "1.00"}}
    ))
    session.add(c)
    await session.flush()
    if owned:
        session.add(CollectionCard(scryfall_id=c.scryfall_id, quantity=1))
    await session.commit()
    return c


def test_distinct_names_strips_and_dedups():
    names = _distinct_names(
        "# comment\n// slashes\nSideboard\n\n2x Black Lotus (LEA) 232 *F*\nblack lotus\n"
        "5 (LEA) 232\nMox Ruby\n   "
    )
    # Quantity, printing hint, and foil marker stripped; the case-insensitive dup collapsed;
    # comment / sideboard / blank lines skipped; a line that becomes empty after stripping the
    # count + printing hint ("5 (LEA) 232") is dropped too.
    assert names == ["Black Lotus", "Mox Ruby"]


@pytest.mark.asyncio
async def test_create_resolves_and_dedups(session):
    await _card(session, "Black Lotus", 1, owned=True)
    await _card(session, "Mox Sapphire", 2, owned=False)
    cl = await create_checklist(
        session, "Power 9", "Black Lotus\nMox Sapphire\nblack lotus\nUnknownCard"
    )
    names = [i.name for i in cl.items]
    assert names == ["Black Lotus", "Mox Sapphire", "UnknownCard"]
    by_name = {i.name: i for i in cl.items}
    assert by_name["Black Lotus"].oracle_id is not None
    assert by_name["UnknownCard"].oracle_id is None


@pytest.mark.asyncio
async def test_coverage_counts(session):
    await _card(session, "Black Lotus", 1, owned=True)
    await _card(session, "Mox Sapphire", 2, owned=False)
    cl = await create_checklist(session, "P9", "Black Lotus\nMox Sapphire\nUnknownCard")
    cov = await checklist_coverage(session, cl)
    assert cov.total == 3 and cov.owned_count == 1 and cov.unmatched == 1
    assert cov.pct_complete == 33 and cov.missing_matched == 1
    assert [r.name for r in cov.missing] == ["Mox Sapphire", "UnknownCard"]


@pytest.mark.asyncio
async def test_coverage_empty_checklist(session):
    cl = await create_checklist(session, "Empty", "")
    cov = await checklist_coverage(session, cl)
    assert cov.total == 0 and cov.pct_complete == 0


@pytest.mark.asyncio
async def test_add_missing_to_wishlist(session):
    await _card(session, "Black Lotus", 1, owned=True)
    await _card(session, "Mox Sapphire", 2, owned=False)
    cl = await create_checklist(session, "P9", "Black Lotus\nMox Sapphire\nUnknownCard")
    assert await add_checklist_missing(session, cl) == 1
    note = await session.scalar(select(WishlistItem.note))
    assert note == "checklist: P9"
