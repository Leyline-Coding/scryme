"""Duplicate-stack detection + merge (#101)."""

import uuid

import pytest
from sqlalchemy import func, select
from src.collection_edit import (
    find_duplicate_stacks,
    merge_all_duplicates,
    merge_duplicate_group,
)
from src.models import Card, CollectionCard
from src.scryfall.mapping import card_to_columns


async def _seed_dupe(session, name="Dup Card"):
    c = Card(**card_to_columns(
        {"id": str(uuid.uuid4()), "oracle_id": str(uuid.uuid4()), "name": name,
         "set": "tst", "collector_number": "1"}
    ))
    session.add(c)
    await session.flush()
    # Same card/finish/condition/language, split across two binders.
    session.add(CollectionCard(scryfall_id=c.scryfall_id, quantity=2, finish="normal",
                               language="en", binder_name="Box A", tags=["keep"]))
    session.add(CollectionCard(scryfall_id=c.scryfall_id, quantity=3, finish="normal",
                               language="en", binder_name="Box B", tags=["trade"]))
    await session.commit()
    return c


async def _count(session, sid=None):
    stmt = select(func.count()).select_from(CollectionCard)
    if sid is not None:
        stmt = stmt.where(CollectionCard.scryfall_id == sid)
    return await session.scalar(stmt)


@pytest.mark.asyncio
async def test_find_and_merge_group(session):
    c = await _seed_dupe(session)
    groups = await find_duplicate_stacks(session)
    assert len(groups) == 1
    g = groups[0]
    assert g.count == 2 and g.total_quantity == 5 and set(g.binders) == {"Box A", "Box B"}

    survivor = await merge_duplicate_group(session, str(c.scryfall_id), "normal", None, "en")
    assert survivor.quantity == 5 and set(survivor.tags) == {"keep", "trade"}
    assert await _count(session, c.scryfall_id) == 1
    assert await find_duplicate_stacks(session) == []


@pytest.mark.asyncio
async def test_merge_all_and_no_false_positives(session):
    await _seed_dupe(session, "Card A")
    await _seed_dupe(session, "Card B")
    # A distinct printing with a single stack must NOT be flagged.
    solo = Card(**card_to_columns(
        {"id": str(uuid.uuid4()), "oracle_id": str(uuid.uuid4()), "name": "Solo", "set": "tst",
         "collector_number": "9"}
    ))
    session.add(solo)
    await session.flush()
    session.add(CollectionCard(scryfall_id=solo.scryfall_id, quantity=1))
    await session.commit()

    assert len(await find_duplicate_stacks(session)) == 2
    assert await merge_all_duplicates(session) == 2
    assert await find_duplicate_stacks(session) == []
    assert await _count(session, solo.scryfall_id) == 1  # solo untouched


@pytest.mark.asyncio
async def test_duplicates_routes(client, session):
    await _seed_dupe(session)
    page = await client.get("/collection/duplicates")
    assert page.status_code == 200 and "Dup Card" in page.text
    resp = await client.post("/collection/duplicates/merge-all", follow_redirects=False)
    assert resp.status_code == 303
    assert await _count(session) == 1
    assert (await client.get("/collection/duplicates")).text.count("Merge</button>") == 0
