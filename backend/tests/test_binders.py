"""Binder browsing tests: grouping by binder_name and the per-binder card view."""

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
async def test_list_binders(client, session):
    await _seed(session)
    resp = await client.get("/collection?tab=binders")
    assert resp.status_code == 200
    assert "Reds" in resp.text
    assert "Unsorted" in resp.text  # the null-binder group


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
    binders_for_card,
    create_binder,
    create_group,
    delete_group,
    grouped_binders,
    remove_card,
    rename_binder,
    set_binder_group,
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
async def test_create_group_and_binder_and_grouping(session):
    g = await create_group(session, "Staples")
    b = await create_binder(session, "Ramp", g.id)
    groups = await grouped_binders(session)
    named = {gv.name: [bv.name for bv in gv.binders] for gv in groups}
    assert "Ramp" in named.get("Staples", [])
    assert b.group_id == g.id


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


@pytest.mark.asyncio
async def test_remove_card_and_rename_and_regroup(session):
    g = await create_group(session, "Staples")
    b = await create_binder(session, "Ramp", g.id)
    owned = await _own_card(session)
    await add_card(session, b.id, str(owned.scryfall_id))

    await remove_card(session, b.id, str(owned.scryfall_id))
    assert await binder_cards(session, b.id) == []

    await rename_binder(session, b.id, "Fast Mana")
    await set_binder_group(session, b.id, None)
    groups = await grouped_binders(session)
    ungrouped = next(gv for gv in groups if gv.name == "Ungrouped")
    assert "Fast Mana" in [bv.name for bv in ungrouped.binders]


@pytest.mark.asyncio
async def test_delete_group_keeps_binders(session):
    g = await create_group(session, "Staples")
    b = await create_binder(session, "Ramp", g.id)
    await delete_group(session, g.id)
    refreshed = await session.get(type(b), b.id)
    assert refreshed is not None and refreshed.group_id is None


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
async def test_binders_home_and_view_pages(client, session):
    b = await create_binder(session, "Removal")
    owned = await _own_card(session, "Swords to Plowshares")
    await add_card(session, b.id, str(owned.scryfall_id))

    home = await client.get("/binders")
    assert home.status_code == 200 and "Removal" in home.text

    view = await client.get(f"/binders/view/{b.id}")
    assert view.status_code == 200 and "Swords to Plowshares" in view.text


@pytest.mark.asyncio
async def test_new_binder_route_creates(client, session):
    resp = await client.post("/binders/new", data={"name": "Cats", "group_id": ""},
                             follow_redirects=False)
    assert resp.status_code == 303
    assert any(bv.name == "Cats" for gv in await grouped_binders(session) for bv in gv.binders)
