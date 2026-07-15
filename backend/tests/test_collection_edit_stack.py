"""Edit a stack (quantity / finish / printing) + remove-copies flow (card-detail editing)."""

import uuid

import pytest
from sqlalchemy import func, select
from src.collection_edit import (
    add_or_increment,
    edit_stack,
    owned_for_oracle,
    remove_copies,
)
from src.models import Card, CollectionCard
from src.scryfall.mapping import card_to_columns


async def _printing(session, oracle, n):
    raw = {"id": str(uuid.uuid4()), "oracle_id": str(oracle), "name": "Sol Ring", "set": "tst",
           "collector_number": str(n), "rarity": "rare", "prices": {"usd": "1.00"}}
    c = Card(**card_to_columns(raw))
    session.add(c)
    await session.commit()
    return c


async def _stacks(session):
    return (await session.execute(select(CollectionCard))).scalars().all()


@pytest.mark.asyncio
async def test_edit_quantity_and_finish(session):
    c = await _printing(session, uuid.uuid4(), 1)
    s = await add_or_increment(session, c.scryfall_id, 2, finish="normal")

    out = await edit_stack(session, s.id, quantity=5, finish="foil")
    assert out.quantity == 5 and out.finish == "foil"
    assert len(await _stacks(session)) == 1


@pytest.mark.asyncio
async def test_edit_finish_merges_into_sibling(session):
    c = await _printing(session, uuid.uuid4(), 1)
    normal = await add_or_increment(session, c.scryfall_id, 2, finish="normal")
    await add_or_increment(session, c.scryfall_id, 1, finish="foil")

    # Change the normal stack to foil → collides with the existing foil stack → merge.
    out = await edit_stack(session, normal.id, finish="foil")
    assert out.quantity == 3 and out.finish == "foil"
    assert len(await _stacks(session)) == 1


@pytest.mark.asyncio
async def test_edit_printing_moves_stack(session):
    oracle = uuid.uuid4()
    p1 = await _printing(session, oracle, 1)
    p2 = await _printing(session, oracle, 2)
    s = await add_or_increment(session, p1.scryfall_id, 1, finish="normal")

    out = await edit_stack(session, s.id, scryfall_id=str(p2.scryfall_id))
    assert str(out.scryfall_id) == str(p2.scryfall_id)
    assert await session.scalar(
        select(func.count()).select_from(CollectionCard)
        .where(CollectionCard.scryfall_id == p1.scryfall_id)) == 0


@pytest.mark.asyncio
async def test_owned_for_oracle_and_remove_copies(session):
    oracle = uuid.uuid4()
    p1 = await _printing(session, oracle, 1)
    p2 = await _printing(session, oracle, 2)
    s1 = await add_or_increment(session, p1.scryfall_id, 3, finish="normal")
    s2 = await add_or_increment(session, p2.scryfall_id, 1, finish="foil")

    owned = await owned_for_oracle(session, oracle)
    assert {str(c.scryfall_id) for _, c in owned} == {str(p1.scryfall_id), str(p2.scryfall_id)}

    # Remove 2 from s1 (leaves 1) and all of s2 (deletes it).
    assert await remove_copies(session, [(s1.id, 2), (s2.id, 5)]) == 3
    remaining = {str(s.scryfall_id): s.quantity for s in await _stacks(session)}
    assert remaining == {str(p1.scryfall_id): 1}


@pytest.mark.asyncio
async def test_edit_and_remove_routes(client, session):
    oracle = uuid.uuid4()
    p1 = await _printing(session, oracle, 1)
    await _printing(session, oracle, 2)   # a sibling printing (offered in the picker/modal)
    s = await add_or_increment(session, p1.scryfall_id, 2, finish="normal")
    sid = str(p1.scryfall_id)

    # Edit: bump to 4 and change to foil.
    resp = await client.post(f"/collection/stack/{s.id}/edit",
                             data={"card_id": sid, "quantity": "4", "finish": "foil"})
    assert resp.status_code == 200
    q, fin = (await session.execute(
        select(CollectionCard.quantity, CollectionCard.finish)
        .where(CollectionCard.id == s.id))).one()
    assert q == 4 and fin == "foil"

    # Remove modal lists both printings.
    modal = await client.get(f"/card/{sid}/remove-modal")
    assert modal.status_code == 200
    assert "Remove from collection" in modal.text

    # Remove 4 copies of the (now-foil) stack → it's gone.
    rm = await client.post("/collection/remove",
                           data={"card_id": sid, "stack_id": str(s.id), "count": "4"})
    assert rm.status_code == 200
    assert await session.scalar(
        select(func.count()).select_from(CollectionCard)
        .where(CollectionCard.id == s.id)) == 0
