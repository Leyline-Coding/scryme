"""Binder tests: legacy import-`binder_name` browse + first-class custom binders (#206)."""

import uuid

import pytest
from src.models import Card, CollectionCard
from src.scryfall.mapping import card_to_columns


async def _seed(session):
    names = [("Alpha", "Reds"), ("Beta", "Reds"), ("Gamma", None)]
    for i, (name, binder) in enumerate(names):
        raw = {"id": str(uuid.uuid4()), "oracle_id": str(uuid.uuid4()), "name": name,
               "set": "TST", "collector_number": str(i), "rarity": "common", "cmc": 1,
               "type_line": "Creature", "colors": ["R"], "color_identity": ["R"]}
        c = Card(**card_to_columns(raw))
        session.add(c)
        await session.flush()
        session.add(CollectionCard(scryfall_id=c.scryfall_id, quantity=2, binder_name=binder))
    await session.commit()


@pytest.mark.asyncio
async def test_view_named_binder(client, session):
    await _seed(session)
    resp = await client.get("/binders/cards", params={"name": "Reds"})
    assert resp.status_code == 200
    assert "Alpha" in resp.text and "Beta" in resp.text
    assert "Gamma" not in resp.text  # not in this binder


@pytest.mark.asyncio
async def test_view_unsorted_binder(client, session):
    await _seed(session)
    resp = await client.get("/binders/cards", params={"name": "__none__"})
    assert resp.status_code == 200
    assert "Gamma" in resp.text
    assert "Alpha" not in resp.text


# --- custom binders (#206) ----------------------------------------------------------------------

from src.binder_service import (  # noqa: E402
    create_binder,
    delete_binder,
    rename_binder,
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
async def test_card_page_binder_routes(client, session):
    b = await create_binder(session, "Ramp")
    owned = await _own_card(session)
    sid = str(owned.scryfall_id)

    add = await client.post(f"/card/{sid}/binder-add", data={"binder_id": str(b.id)})
    assert add.status_code == 200
    assert "Ramp" in add.text

    rm = await client.post(f"/card/{sid}/binder-remove", data={"binder_id": str(b.id)})
    assert rm.status_code == 200


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
