"""Storage locations + color-identity organizer (#160)."""

import uuid

import pytest
from sqlalchemy import select
from src.collection_edit import (
    add_or_increment,
    color_identity_group,
    location_summary,
    organize_by_color_identity,
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
async def test_organize_by_color_identity_and_loc_search(session):
    warhorse = await _own(session, "Phyrexian Warhorse", ("W", "B"))  # -> Orzhov
    await _own(session, "Mono Black", ("B",))
    await _own(session, "Rock", ())

    assert await organize_by_color_identity(session) == 3
    locs = {str(s.scryfall_id): s.location for s in
            (await session.execute(select(CollectionCard))).scalars()}
    assert locs[str(warhorse.scryfall_id)] == "Orzhov"
    summary = {s.location: s.quantity for s in await location_summary(session)}
    assert summary["Orzhov"] == 1 and summary["Colorless"] == 1

    res = await run_search(session, "loc:orzhov", scope=SearchScope.COLLECTION)
    assert "Phyrexian Warhorse" in [c.name for c in res.cards]
    assert "Mono Black" not in [c.name for c in res.cards]


@pytest.mark.asyncio
async def test_location_is_part_of_stack_identity(session):
    c = await _own(session, "Splittable", ("R",), owned=False)
    a = await add_or_increment(session, c.scryfall_id, 2, location="Box A")
    b = await add_or_increment(session, c.scryfall_id, 3, location="Box B")
    assert a.id != b.id                                      # split across locations
    again = await add_or_increment(session, c.scryfall_id, 1, location="Box A")
    assert again.id == a.id and again.quantity == 3          # same location increments


@pytest.mark.asyncio
async def test_organize_route(client, session):
    await _own(session, "WB Card", ("W", "B"))
    resp = await client.post("/collection/organize-by-identity", follow_redirects=False)
    assert resp.status_code == 303
    page = await client.get("/collection/locations")
    assert page.status_code == 200 and "Orzhov" in page.text
