"""Custom checklists: creation/resolution, coverage, add-missing-to-wishlist, and routes."""

import uuid

import pytest
from sqlalchemy import func, select
from src.checklists import (
    add_checklist_missing,
    checklist_coverage,
    create_checklist,
)
from src.models import Card, CollectionCard, WishlistItem
from src.scryfall.mapping import card_to_columns


async def _card(session, name, n, owned=False):
    raw = {"id": str(uuid.uuid4()), "oracle_id": str(uuid.uuid4()), "name": name, "set": "tst",
           "collector_number": str(n), "rarity": "rare", "prices": {"usd": "1.00"}}
    c = Card(**card_to_columns(raw))
    session.add(c)
    await session.flush()
    if owned:
        session.add(CollectionCard(scryfall_id=c.scryfall_id, quantity=1))
    await session.commit()
    return c


@pytest.mark.asyncio
async def test_create_resolves_and_dedups(session):
    await _card(session, "Black Lotus", 1, owned=True)
    await _card(session, "Mox Sapphire", 2, owned=False)
    cl = await create_checklist(
        session, "Power 9", "Black Lotus\nMox Sapphire\nblack lotus\nUnknownCard"
    )
    names = [i.name for i in cl.items]
    assert names == ["Black Lotus", "Mox Sapphire", "UnknownCard"]  # dup collapsed
    by_name = {i.name: i for i in cl.items}
    assert by_name["Black Lotus"].oracle_id is not None
    assert by_name["UnknownCard"].oracle_id is None


@pytest.mark.asyncio
async def test_coverage_counts(session):
    await _card(session, "Black Lotus", 1, owned=True)
    await _card(session, "Mox Sapphire", 2, owned=False)
    cl = await create_checklist(session, "P9", "Black Lotus\nMox Sapphire\nUnknownCard")
    cov = await checklist_coverage(session, cl)
    assert cov.total == 3
    assert cov.owned_count == 1
    assert cov.unmatched == 1
    assert cov.pct_complete == 33
    assert cov.missing_matched == 1  # Mox Sapphire (matched, not owned); UnknownCard isn't matched
    assert [r.name for r in cov.missing] == ["Mox Sapphire", "UnknownCard"]


@pytest.mark.asyncio
async def test_add_missing_to_wishlist(session):
    await _card(session, "Black Lotus", 1, owned=True)
    await _card(session, "Mox Sapphire", 2, owned=False)
    cl = await create_checklist(session, "P9", "Black Lotus\nMox Sapphire\nUnknownCard")
    added = await add_checklist_missing(session, cl)
    assert added == 1  # only the matched-but-missing card
    note = await session.scalar(select(WishlistItem.note))
    assert note == "checklist: P9"


@pytest.mark.asyncio
async def test_checklist_routes(client, session):
    await _card(session, "Black Lotus", 1, owned=True)
    create = await client.post(
        "/checklists", data={"name": "P9", "cards": "Black Lotus\nMox Pearl"},
        follow_redirects=False,
    )
    assert create.status_code == 303
    loc = create.headers["location"]

    page = await client.get(loc)
    assert page.status_code == 200
    assert "P9" in page.text and "% complete" in page.text and "Black Lotus" in page.text

    listing = await client.get("/checklists")
    assert "P9" in listing.text


@pytest.mark.asyncio
async def test_read_only_blocks_create(client, session, monkeypatch):
    from src.config import get_settings
    monkeypatch.setattr(get_settings(), "read_only", True)
    resp = await client.post("/checklists", data={"name": "x", "cards": "Black Lotus"})
    assert resp.status_code == 403
    assert await session.scalar(select(func.count()).select_from(WishlistItem)) == 0
