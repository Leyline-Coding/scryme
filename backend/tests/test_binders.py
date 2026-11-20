"""Binder tests: legacy import-`binder_name` browse + first-class custom binders (#206)."""

import uuid

import pytest
from sqlalchemy import select
from src.models import Binder, Card, CollectionCard, Location
from src.scryfall.mapping import card_to_columns


async def _seed(session):
    names = [("Alpha", "Reds"), ("Beta", "Reds"), ("Gamma", None)]
    for i, (name, binder_name) in enumerate(names):
        if binder_name is not None:
            # Only try to find/create a binder if the name isn't None
            binder = await session.scalar(select(Binder).where(Binder.name == binder_name))
            if not binder:
              binder = Binder(name=binder_name)
              session.add(binder)
              await session.commit()  # Commit to ensure the binder is saved so that it gets an ID
            location_id = await session.scalar(select(Location.id).where((Location.type == "binder") & (Location.foreign_id == binder.id)))
        else:
            # For cards with no binder, they should go into unfiled location
            location_id = await session.scalar(select(Location.id).where((Location.type == "unfiled") & (Location.foreign_id.is_(None))))
        raw = {"id": str(uuid.uuid4()), "oracle_id": str(uuid.uuid4()), "name": name,
               "set": "TST", "collector_number": str(i), "rarity": "common", "cmc": 1,
               "type_line": "Creature", "colors": ["R"], "color_identity": ["R"]}
        c = Card(**card_to_columns(raw))
        session.add(c)
        await session.flush()
        session.add(CollectionCard(scryfall_id=c.scryfall_id, quantity=2, location=location_id))
    await session.commit()


# --- custom binders (#206) ----------------------------------------------------------------------

from src.binder_service import (  # noqa: E402
    create_binder,
)


async def _own_card(session, name="Sol Ring", owned=True):
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
async def test_create_binder_unique_and_summaries(session):
    assert await create_binder(session, "Ramp") is not None
    assert await create_binder(session, "  ") is None          # blank
    assert await create_binder(session, "Ramp") is None        # duplicate name


@pytest.mark.asyncio
async def test_new_binder_route_creates(client, session):
    resp = await client.post("/binders/new", data={"name": "Cats"}, follow_redirects=False)
    assert resp.status_code == 303


@pytest.mark.asyncio
async def test_bulk_binder_action_from_search(client, session):
    b = await create_binder(session, "Staples")
    owned = await _own_card(session, "Sol Ring")
    resp = await client.post(
        "/collection/bulk",
        data={"bulk_action": "binder", "binder_id": str(b.id),
              "scryfall_ids": [str(owned.scryfall_id)], "q": "", "scope": "collection"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
