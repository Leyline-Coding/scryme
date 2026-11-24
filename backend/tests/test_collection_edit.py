"""Manual collection editing: add/increment, quantity nudges, delete, bulk, and routes."""

import uuid

import pytest
from sqlalchemy import func, select
from src.box_service import create_box
from src.collection_edit import (
    add_or_increment,
    adjust_quantity,
    bulk_add_to_collection,
    delete_stack,
)
from src.models import Card, CollectionCard, Location
from src.scryfall.mapping import card_to_columns
from src.tags import modify_stacks_tag


async def _card(session, name="Aaa", n=1):
    raw = {"id": str(uuid.uuid4()), "oracle_id": str(uuid.uuid4()), "name": name, "set": "tst",
           "collector_number": str(n), "rarity": "rare", "prices": {"usd": "1.00"}}
    c = Card(**card_to_columns(raw))
    session.add(c)
    await session.commit()
    return c


async def _count(session, sid):
    return await session.scalar(
        select(func.coalesce(func.sum(CollectionCard.quantity), 0))
        .where(CollectionCard.scryfall_id == sid)
    )


@pytest.mark.asyncio
async def test_add_then_increment_same_stack(session):
    c = await _card(session)
    box = await create_box(session, "Box")
    await session.commit()
    assert box is not None
    location_id = await session.scalar(
        select(Location.id).where((Location.type == "box") & (Location.foreign_id == box.id))
    )

    s1 = await add_or_increment(session, c.scryfall_id, 2, finish="foil", location=location_id)
    assert s1 is not None
    assert s1.quantity == 2 and s1.source_format == "manual"
    # Same finish+binder -> increments the same stack, not a new one.
    s2 = await add_or_increment(session, c.scryfall_id, 3, finish="foil", location=location_id)
    assert s2 is not None
    assert s2.id == s1.id and s2.quantity == 5
    assert await session.scalar(
        select(func.count()).select_from(CollectionCard)
        .where(CollectionCard.scryfall_id == c.scryfall_id)
    ) == 1


@pytest.mark.asyncio
async def test_add_blank_fields_normalize_to_null(session):
    c = await _card(session)
    s1 = await add_or_increment(session, c.scryfall_id, 1, condition="  ", location=None)
    assert s1 is not None
    assert s1.condition is None and s1.location is None
    # A second add with empty strings matches the same NULL-keyed stack.
    s2 = await add_or_increment(session, c.scryfall_id, 1, condition="", location=None)
    assert s2 is not None
    assert s2.id == s1.id and s2.quantity == 2


@pytest.mark.asyncio
async def test_add_unknown_card_returns_none(session):
    assert await add_or_increment(session, uuid.uuid4(), 1) is None


@pytest.mark.asyncio
async def test_adjust_and_delete_at_zero(session):
    c = await _card(session)
    assert c is not None
    s = await add_or_increment(session, c.scryfall_id, 2)
    assert s is not None
    await adjust_quantity(session, s.id, 1)
    assert await _count(session, c.scryfall_id) == 3
    await adjust_quantity(session, s.id, -3)  # 3 -> 0 deletes the stack
    assert await _count(session, c.scryfall_id) == 0
    assert await session.get(CollectionCard, s.id) is None


@pytest.mark.asyncio
async def test_delete_stack(session):
    c = await _card(session)
    assert c is not None
    s = await add_or_increment(session, c.scryfall_id, 4)
    assert s is not None
    sid = await delete_stack(session, s.id)
    assert str(sid) == str(c.scryfall_id)
    assert await _count(session, c.scryfall_id) == 0


@pytest.mark.asyncio
async def test_bulk_add_and_tag(session):
    a, b = await _card(session, "Aaa", 1), await _card(session, "Bbb", 2)
    added = await bulk_add_to_collection(session, [str(a.scryfall_id), str(b.scryfall_id)], 1)
    assert added == 2
    assert await _count(session, a.scryfall_id) == 1

    collection_card_ids = (
        await session.execute(
            select(CollectionCard.id).where(
                CollectionCard.scryfall_id.in_((a.scryfall_id,b.scryfall_id))
            )
        )
    ).scalars().all()

    tagged = await modify_stacks_tag(session, collection_card_ids, "Trade")
    assert tagged == 2
    tags = await session.scalar(
        select(CollectionCard.tags).where(CollectionCard.scryfall_id == a.scryfall_id)
    )
    assert tags == ["trade"]

    untagged = await modify_stacks_tag(session, collection_card_ids, "Trade", remove=True)
    assert untagged == 2


@pytest.mark.asyncio
async def test_card_edit_routes(client, session):
    c = await _card(session)
    cid = str(c.scryfall_id)
    add = await client.post("/collection/add", data={"scryfall_id": cid, "quantity": 2,
                                                     "finish": "normal"})
    assert add.status_code == 200
    assert "This printing" in add.text and "2 of 2 total" in add.text

    stack_id = await session.scalar(select(CollectionCard.id)
                                    .where(CollectionCard.scryfall_id == c.scryfall_id))
    bumped = await client.post(f"/collection/stack/{stack_id}/adjust", data={"delta": 1})
    assert "3 of 3 total" in bumped.text

    gone = await client.post(f"/collection/stack/{stack_id}/delete")
    assert "don't own this printing yet" in gone.text


@pytest.mark.asyncio
async def test_bulk_route_redirects_and_applies(client, session):
    c = await _card(session)
    resp = await client.post(
        "/collection/bulk",
        data={"bulk_action": "add", "scryfall_ids": [str(c.scryfall_id)], "q": "t:rare"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert "/search?" in resp.headers["location"] and "t%3Arare" in resp.headers["location"]
    assert await _count(session, c.scryfall_id) == 1


@pytest.mark.asyncio
async def test_read_only_blocks_edits(client, session, monkeypatch):
    from src.config import get_settings
    monkeypatch.setattr(get_settings(), "read_only", True)
    c = await _card(session)
    resp = await client.post("/collection/add", data={"scryfall_id": str(c.scryfall_id)})
    assert resp.status_code == 403
