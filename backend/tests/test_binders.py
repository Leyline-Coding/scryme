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
    add_card,
    binder_cards,
    binder_summaries,
    binders_for_card,
    bulk_add_to_binder,
    create_binder,
    delete_binder,
    remove_card,
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
    sums = await binder_summaries(session)
    assert [s.name for s in sums] == ["Ramp"]
    assert sums[0].count == 0


@pytest.mark.asyncio
async def test_add_owned_card_only(session):
    b = await create_binder(session, "Ramp")
    owned = await _own_card(session, "Sol Ring", owned=True)
    unowned = await _own_card(session, "Mana Crypt", owned=False)

    assert await add_card(session, b.id, str(owned.scryfall_id)) is True
    assert await add_card(session, b.id, str(owned.scryfall_id)) is False  # already in
    assert await add_card(session, b.id, str(unowned.scryfall_id)) is False  # not owned

    cards = await binder_cards(session, b.id)
    assert [c.name for c in cards] == ["Sol Ring"]
    assert await binders_for_card(session, str(owned.scryfall_id)) == {b.id}
    assert (await binder_summaries(session))[0].count == 1


@pytest.mark.asyncio
async def test_bulk_add_to_binder_owned_only(session):
    b = await create_binder(session, "Staples")
    a = await _own_card(session, "Sol Ring", owned=True)
    c = await _own_card(session, "Arcane Signet", owned=True)
    unowned = await _own_card(session, "Mana Crypt", owned=False)

    added = await bulk_add_to_binder(
        session, b.id, [str(a.scryfall_id), str(c.scryfall_id), str(unowned.scryfall_id)]
    )
    assert added == 2  # unowned skipped
    # re-adding is idempotent
    assert await bulk_add_to_binder(session, b.id, [str(a.scryfall_id)]) == 0
    names = {card.name for card in await binder_cards(session, b.id)}
    assert names == {"Sol Ring", "Arcane Signet"}


@pytest.mark.asyncio
async def test_remove_card_rename_delete(session):
    b = await create_binder(session, "Ramp")
    owned = await _own_card(session)
    await add_card(session, b.id, str(owned.scryfall_id))

    await remove_card(session, b.id, str(owned.scryfall_id))
    assert await binder_cards(session, b.id) == []

    await rename_binder(session, b.id, "Fast Mana")
    assert (await binder_summaries(session))[0].name == "Fast Mana"

    await delete_binder(session, b.id)
    assert await binder_summaries(session) == []


@pytest.mark.asyncio
async def test_card_page_binder_routes(client, session):
    b = await create_binder(session, "Ramp")
    owned = await _own_card(session)
    sid = str(owned.scryfall_id)

    add = await client.post(f"/card/{sid}/binder-add", data={"binder_id": str(b.id)})
    assert add.status_code == 200
    assert "Ramp" in add.text
    assert await binders_for_card(session, sid) == {b.id}

    rm = await client.post(f"/card/{sid}/binder-remove", data={"binder_id": str(b.id)})
    assert rm.status_code == 200
    assert await binders_for_card(session, sid) == set()


@pytest.mark.asyncio
async def test_binders_tab_and_view_pages(client, session):
    b = await create_binder(session, "Removal")
    owned = await _own_card(session, "Swords to Plowshares")
    await add_card(session, b.id, str(owned.scryfall_id))

    # /binders redirects to the collection Binders tab, which lists custom binders.
    tab = await client.get("/collection?tab=binders")
    assert tab.status_code == 200 and "Removal" in tab.text

    view = await client.get(f"/binders/view/{b.id}")
    assert view.status_code == 200 and "Swords to Plowshares" in view.text


@pytest.mark.asyncio
async def test_new_binder_route_creates(client, session):
    resp = await client.post("/binders/new", data={"name": "Cats"}, follow_redirects=False)
    assert resp.status_code == 303
    assert any(s.name == "Cats" for s in await binder_summaries(session))


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
    assert await binders_for_card(session, str(owned.scryfall_id)) == {b.id}
