"""Import-ownership tests: mark a deck unowned / fully owned / partially owned (checklist)."""

import uuid

import pytest
from sqlalchemy import func, select
from src.config import get_settings
from src.decks import resolve_ownership_rows
from src.models import Card, CollectionCard, Deck
from src.scryfall.mapping import card_to_columns


async def _seed_cards(session):
    sol = {"id": str(uuid.uuid4()), "oracle_id": str(uuid.uuid4()), "name": "Sol Ring",
           "set": "CMM", "collector_number": "1", "type_line": "Artifact", "cmc": 1,
           "color_identity": [], "legalities": {"commander": "legal"}, "prices": {"usd": "1"}}
    bear = {"id": str(uuid.uuid4()), "oracle_id": str(uuid.uuid4()), "name": "Grizzly Bears",
            "set": "M11", "collector_number": "2", "type_line": "Creature — Bear", "cmc": 2,
            "color_identity": ["G"], "legalities": {"commander": "legal"}, "prices": {"usd": "1"}}
    cards = {}
    for raw in (sol, bear):
        c = Card(**card_to_columns(raw))
        session.add(c)
        cards[raw["name"]] = c
    await session.commit()
    return cards


async def _owned_qty(session, card):
    return await session.scalar(
        select(func.coalesce(func.sum(CollectionCard.quantity), 0))
        .where(CollectionCard.scryfall_id == card.scryfall_id)
    )


@pytest.mark.asyncio
async def test_resolve_ownership_rows_merges_boards_and_resolves(session):
    await _seed_cards(session)
    rows = await resolve_ownership_rows(
        session, "2 Sol Ring\nSideboard\n1 Sol Ring\n1 Grizzly Bears\n1 Nonexistent Card")
    by_name = {r.name: r for r in rows}
    assert by_name["Sol Ring"].quantity == 3       # main + sideboard summed
    assert by_name["Sol Ring"].matched is True and by_name["Sol Ring"].scryfall_id
    assert by_name["Nonexistent Card"].matched is False
    assert by_name["Nonexistent Card"].scryfall_id is None


@pytest.mark.asyncio
async def test_create_unowned_does_not_touch_collection(client, session):
    cards = await _seed_cards(session)
    resp = await client.post("/decks", data={"name": "D", "decklist": "1 Sol Ring",
                                             "ownership": "unowned"}, follow_redirects=False)
    assert resp.status_code == 303
    assert await _owned_qty(session, cards["Sol Ring"]) == 0


@pytest.mark.asyncio
async def test_create_fully_owned_adds_all_to_collection(client, session):
    cards = await _seed_cards(session)
    resp = await client.post(
        "/decks", data={"name": "D", "decklist": "3 Sol Ring\n1 Grizzly Bears\n1 Bogus",
                        "ownership": "full"}, follow_redirects=False)
    assert resp.status_code == 303
    assert await _owned_qty(session, cards["Sol Ring"]) == 3      # added at deck quantity
    assert await _owned_qty(session, cards["Grizzly Bears"]) == 1
    # The deck was created and shows full coverage for the matched cards.
    deck_id = int(resp.headers["location"].rsplit("/", 1)[1])
    page = await client.get(f"/decks/{deck_id}")
    assert page.status_code == 200


@pytest.mark.asyncio
async def test_create_partial_renders_checklist_without_creating(client, session):
    await _seed_cards(session)
    resp = await client.post(
        "/decks",
        data={"name": "Mine", "decklist": "1 Sol Ring\n1 Grizzly Bears", "ownership": "partial"})
    assert resp.status_code == 200
    assert "Which cards do you own" in resp.text
    assert "Sol Ring" in resp.text and 'name="owned"' in resp.text
    assert await session.scalar(select(func.count()).select_from(Deck)) == 0  # not created yet


@pytest.mark.asyncio
async def test_owned_confirm_creates_and_adds_checked(client, session):
    cards = await _seed_cards(session)
    resp = await client.post(
        "/decks/owned-confirm",
        data={"name": "Mine", "decklist": "2 Sol Ring\n1 Grizzly Bears",
              "owned": [f"{cards['Sol Ring'].scryfall_id}|2"]},  # only Sol Ring checked
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert await _owned_qty(session, cards["Sol Ring"]) == 2
    assert await _owned_qty(session, cards["Grizzly Bears"]) == 0  # unchecked -> not added
    assert await session.scalar(select(func.count()).select_from(Deck)) == 1


@pytest.mark.asyncio
async def test_owned_confirm_tolerates_bad_tokens(client, session):
    await _seed_cards(session)
    resp = await client.post(
        "/decks/owned-confirm",
        data={"name": "Mine", "decklist": "1 Sol Ring",
              "owned": ["", "|3", f"{uuid.uuid4()}|x"]},  # blank, no-sid, non-numeric qty
        follow_redirects=False,
    )
    assert resp.status_code == 303  # nothing valid to add, deck still created


@pytest.mark.asyncio
async def test_import_url_respects_ownership(client, session, monkeypatch):
    cards = await _seed_cards(session)

    async def fake_fetch(url, **kw):
        return "Imported", "1 Sol Ring"

    monkeypatch.setattr("src.routes.decks.fetch_deck_from_url", fake_fetch)
    # Fully owned via URL import adds to the collection.
    resp = await client.post("/decks/import-url",
                             data={"url": "https://moxfield.com/decks/x", "ownership": "full"},
                             follow_redirects=False)
    assert resp.status_code == 303
    assert await _owned_qty(session, cards["Sol Ring"]) == 1
    # Partial via URL import shows the checklist.
    resp = await client.post("/decks/import-url",
                             data={"url": "https://moxfield.com/decks/x", "ownership": "partial"})
    assert resp.status_code == 200 and "Which cards do you own" in resp.text


@pytest.mark.asyncio
async def test_ownership_routes_read_only(client, session, monkeypatch):
    await _seed_cards(session)
    monkeypatch.setattr(get_settings(), "read_only", True)
    assert (await client.post("/decks", data={"name": "D", "decklist": "1 Sol Ring",
                                             "ownership": "full"})).status_code == 403
    assert (await client.post("/decks/owned-confirm",
                              data={"name": "D", "decklist": "1 Sol Ring"})).status_code == 403
