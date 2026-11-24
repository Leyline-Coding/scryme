"""Storage locations + color-identity organizer (#160)."""

import uuid

import pytest
from sqlalchemy import select
from src.collection_edit import (
    add_or_increment,
    color_identity_group,
)
from src.models import Card, CollectionCard
from src.scryfall.mapping import card_to_columns
from src.search import SearchScope
from src.search.engine import run_search


def test_color_identity_group():
    assert color_identity_group(["W", "B"]) == "Orzhov"      # Phyrexian Warhorse
    assert color_identity_group(["B", "W"]) == "Orzhov"      # order-independent
    assert color_identity_group(["B"]) == "Black"
    assert color_identity_group([]) == "Colorless"
    assert color_identity_group(["W", "B", "R"]) == "Mardu"
    assert color_identity_group(["W", "U", "B", "R"]) == "Four-color"
    assert color_identity_group(["W", "U", "B", "R", "G"]) == "Five-color"


async def _own(session, name, ci, owned=True):
    c = Card(**card_to_columns(
        {"id": str(uuid.uuid4()), "oracle_id": str(uuid.uuid4()), "name": name, "set": "tst",
         "collector_number": str(abs(hash(name)) % 9999), "color_identity": list(ci)}
    ))
    session.add(c)
    await session.flush()
    if owned:
        session.add(CollectionCard(scryfall_id=c.scryfall_id, quantity=1))
    await session.commit()
    return c


@pytest.mark.asyncio
async def test_location_is_part_of_stack_identity(session):
    c = await _own(session, "Splittable", ("R",), owned=False)
    a = await add_or_increment(session, c.scryfall_id, quantity=2, finish="normal", location=22)
    b = await add_or_increment(session, c.scryfall_id, quantity=3, finish="foil", location=22)
    assert a is not None
    assert b is not None
    assert a.id != b.id                                      # split across locations
    again = await add_or_increment(session, c.scryfall_id, quantity=1, finish="normal", location=22)
    assert again is not None
    assert again.id == a.id and again.quantity == 3          # same location increments
