"""Storage boxes, the unified location picker, and the Tags tab (#160)."""

import uuid

import pytest
from sqlalchemy import select
from src.binder_service import binders_for_card, create_binder
from src.box_service import (
    box_summaries,
    create_box,
    delete_box,
    other_locations,
    rename_box,
)
from src.models import Card, CollectionCard, Deck
from src.scryfall.mapping import card_to_columns
from src.tags import add_card_tag, tag_summaries


async def _own(session, name="Sol Ring", rarity="rare", location=None):
    c = Card(**card_to_columns(
        {"id": str(uuid.uuid4()), "oracle_id": str(uuid.uuid4()), "name": name,
         "set": "cmr", "collector_number": str(abs(hash(name)) % 9999), "rarity": rarity}
    ))
    session.add(c)
    await session.flush()
    session.add(CollectionCard(scryfall_id=c.scryfall_id, quantity=2, location=location))
    await session.commit()
    return c


@pytest.mark.asyncio
async def test_box_crud_and_summaries(session):
    assert await create_box(session, "Bulk") is not None
    assert await create_box(session, "  ") is None            # blank
    assert await create_box(session, "Bulk") is None          # duplicate
    await _own(session, "Sol Ring", location="Bulk")
    sums = {b.name: b.quantity for b in await box_summaries(session)}
    assert sums == {"Bulk": 2}


@pytest.mark.asyncio
async def test_rename_box_cascades_location(session):
    box = await create_box(session, "Bulk")
    card = await _own(session, "Sol Ring", location="Bulk")
    await rename_box(session, box.id, "Storage A")
    loc = await session.scalar(
        select(CollectionCard.location).where(CollectionCard.scryfall_id == card.scryfall_id)
    )
    assert loc == "Storage A"


@pytest.mark.asyncio
async def test_delete_box_unfiles_cards(session):
    box = await create_box(session, "Bulk")
    card = await _own(session, "Sol Ring", location="Bulk")
    await delete_box(session, box.id)
    loc = await session.scalar(
        select(CollectionCard.location).where(CollectionCard.scryfall_id == card.scryfall_id)
    )
    assert loc is None
    assert await box_summaries(session) == []


@pytest.mark.asyncio
async def test_other_locations_lists_non_registry(session):
    await _own(session, "Random", location="Junk Drawer")   # no matching box
    others = {o.name for o in await other_locations(session)}
    assert "Junk Drawer" in others


@pytest.mark.asyncio
async def test_locate_stack_into_box_binder_deck(client, session):
    card = await _own(session, "Sol Ring")
    stack = await session.scalar(select(CollectionCard.id))
    sid = str(card.scryfall_id)
    box = await create_box(session, "Bulk")
    binder = await create_binder(session, "Ramp")
    deck = Deck(name="Atraxa")
    session.add(deck)
    await session.commit()

    # Box -> sets the stack's physical location.
    await client.post(f"/collection/stack/{stack}/locate",
                      data={"location_choice": f"box:{box.name}"})
    loc = await session.scalar(select(CollectionCard.location).where(CollectionCard.id == stack))
    assert loc == "Bulk"

    # Binder -> adds the printing to the binder.
    await client.post(f"/collection/stack/{stack}/locate",
                      data={"location_choice": f"binder:{binder.id}"})
    assert await binders_for_card(session, sid) == {binder.id}

    # Deck -> adds a decklist line.
    await client.post(f"/collection/stack/{stack}/locate",
                      data={"location_choice": f"deck:{deck.id}"})
    await session.refresh(deck)
    assert any(dc.name == "Sol Ring" for dc in deck.cards)

    # Empty -> unfile.
    await client.post(f"/collection/stack/{stack}/locate", data={"location_choice": ""})
    loc = await session.scalar(select(CollectionCard.location).where(CollectionCard.id == stack))
    assert loc is None


@pytest.mark.asyncio
async def test_tag_summaries_and_tab(client, session):
    card = await _own(session, "Sol Ring")
    await add_card_tag(session, card.scryfall_id, "ramp")
    await add_card_tag(session, card.scryfall_id, "artifact")
    sums = {t.name: t.quantity for t in await tag_summaries(session)}
    assert sums == {"artifact": 2, "ramp": 2}

    resp = await client.get("/collection?tab=tags")
    assert resp.status_code == 200
    assert "ramp" in resp.text and "artifact" in resp.text


@pytest.mark.asyncio
async def test_locations_hub_page(client, session):
    await create_box(session, "Bulk")
    resp = await client.get("/collection/locations")
    assert resp.status_code == 200
    assert "Bulk" in resp.text and "Boxes" in resp.text
