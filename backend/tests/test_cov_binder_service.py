"""Coverage tests for src.binder_service: CRUD, membership, and the missing-binder guards."""

import uuid

import pytest
from src.binder_service import (
    add_card,
    all_binders,
    binder_cards,
    binder_summaries,
    binders_for_card,
    bulk_add_to_binder,
    create_binder,
    delete_binder,
    remove_card,
    rename_binder,
)
from src.models import Card, CollectionCard
from src.scryfall.mapping import card_to_columns


async def _own(session, name="Sol Ring", owned=True):
    c = Card(**card_to_columns(
        {"id": str(uuid.uuid4()), "oracle_id": str(uuid.uuid4()), "name": name,
         "set": "cmr", "collector_number": str(abs(hash(name)) % 9999)}
    ))
    session.add(c)
    await session.flush()
    if owned:
        session.add(CollectionCard(scryfall_id=c.scryfall_id, quantity=1))
    await session.commit()
    return c


@pytest.mark.asyncio
async def test_create_rename_delete_and_summaries(session):
    assert await create_binder(session, "Ramp") is not None
    assert await create_binder(session, "  ") is None       # blank
    assert await create_binder(session, "Ramp") is None      # duplicate
    sums = await binder_summaries(session)
    assert [s.name for s in sums] == ["Ramp"] and sums[0].count == 0
    assert len(await all_binders(session)) == 1

    b = sums[0]
    await rename_binder(session, b.id, "Fast Mana")
    assert (await binder_summaries(session))[0].name == "Fast Mana"
    # Renaming to blank is ignored.
    await rename_binder(session, b.id, "   ")
    assert (await binder_summaries(session))[0].name == "Fast Mana"
    # Renaming a missing binder is a no-op.
    await rename_binder(session, 999999, "Nope")

    await delete_binder(session, b.id)
    assert await binder_summaries(session) == []
    await delete_binder(session, 999999)  # missing -> no-op


@pytest.mark.asyncio
async def test_add_card_guards_and_membership(session):
    b = await create_binder(session, "Ramp")
    owned = await _own(session, "Sol Ring", owned=True)
    unowned = await _own(session, "Mana Crypt", owned=False)

    # Missing binder -> False.
    assert await add_card(session, 999999, str(owned.scryfall_id)) is False
    assert await add_card(session, b.id, str(owned.scryfall_id)) is True
    assert await add_card(session, b.id, str(owned.scryfall_id)) is False  # already present
    assert await add_card(session, b.id, str(unowned.scryfall_id)) is False  # not owned

    assert [c.name for c in await binder_cards(session, b.id)] == ["Sol Ring"]
    assert await binders_for_card(session, str(owned.scryfall_id)) == {b.id}

    await remove_card(session, b.id, str(owned.scryfall_id))
    assert await binder_cards(session, b.id) == []


@pytest.mark.asyncio
async def test_bulk_add_guards(session):
    b = await create_binder(session, "Staples")
    a = await _own(session, "Sol Ring", owned=True)
    c = await _own(session, "Arcane Signet", owned=True)
    unowned = await _own(session, "Mana Crypt", owned=False)

    # Missing binder -> 0.
    assert await bulk_add_to_binder(session, 999999, [str(a.scryfall_id)]) == 0
    # Empty id list -> 0.
    assert await bulk_add_to_binder(session, b.id, []) == 0

    added = await bulk_add_to_binder(
        session, b.id, [str(a.scryfall_id), str(c.scryfall_id), str(unowned.scryfall_id)]
    )
    assert added == 2  # unowned skipped
    assert await bulk_add_to_binder(session, b.id, [str(a.scryfall_id)]) == 0  # idempotent
    names = {card.name for card in await binder_cards(session, b.id)}
    assert names == {"Sol Ring", "Arcane Signet"}
