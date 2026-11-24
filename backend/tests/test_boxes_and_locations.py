"""Storage boxes, the unified location picker, and the Tags tab (#160)."""

import uuid

import pytest
from sqlalchemy import select
from src.box_service import (
    create_box,
)
from src.models import Card, CollectionCard, Deck, Location
from src.scryfall.mapping import card_to_columns


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
async def test_locate_stack_into_box_binder_deck(client, session):
    await _own(session, "Sol Ring")
    stack_id = await session.scalar(select(CollectionCard.id))
    box = await create_box(session, "Bulk")
    assert box is not None


    deck = Deck(name="Atraxa")
    session.add(deck)
    await session.commit()
    location_id = await session.scalar(
        select(Location.id).where((Location.type == "box") & (Location.foreign_id == box.id))
    )

    # Box -> sets the stack's physical location.
    await client.post("/collection/bulk",
                      data={"bulk_action": "location",
                            "stack_ids": [stack_id],
                            "bulk_location_id": location_id
                      })
    loc = await session.scalar(select(CollectionCard.location).where(CollectionCard.id == stack_id))
    assert loc == location_id
