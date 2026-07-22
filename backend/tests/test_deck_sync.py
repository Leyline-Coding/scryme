"""Owned-deck ↔ collection sync tests (#298)."""

import uuid

import pytest
from sqlalchemy import func, select
from src.collection_edit import adjust_owned
from src.config import get_settings
from src.deck_sync import syncs
from src.models import Card, CollectionCard, Deck, DeckCard
from src.scryfall.mapping import card_to_columns


def _raw(name, ci=("G",), commander="legal"):
    return {"id": str(uuid.uuid4()), "oracle_id": str(uuid.uuid4()), "name": name, "set": "TST",
            "collector_number": "1", "type_line": "Creature", "cmc": 2, "color_identity": list(ci),
            "prices": {"usd": "1.00"}, "legalities": {"commander": commander}}


async def _add_card(session, raw):
    c = Card(**card_to_columns(raw))
    session.add(c)
    await session.commit()
    return c


async def _owned_qty(session, card):
    return await session.scalar(
        select(func.coalesce(func.sum(CollectionCard.quantity), 0))
        .where(CollectionCard.scryfall_id == card.scryfall_id)
    )


# --- adjust_owned ------------------------------------------------------------

@pytest.mark.asyncio
async def test_adjust_owned_add_remove_delete(session):
    card = await _add_card(session, _raw("Sol Ring"))
    sid = str(card.scryfall_id)
    await adjust_owned(session, sid, 3)
    assert await _owned_qty(session, card) == 3
    await adjust_owned(session, sid, 2)          # increments the same default stack
    assert await _owned_qty(session, card) == 5
    await adjust_owned(session, sid, -2)
    assert await _owned_qty(session, card) == 3
    await adjust_owned(session, sid, -10)        # over-remove -> stack deleted
    assert await _owned_qty(session, card) == 0
    await adjust_owned(session, sid, -1)         # no stack -> no-op
    await adjust_owned(session, sid, 0)          # no-op
    assert await _owned_qty(session, card) == 0


# --- sync gating -------------------------------------------------------------

def test_syncs_gating():
    def dc(owned=True, sid=uuid.uuid4()):
        return DeckCard(name="x", quantity=1, board="main", owned=owned, scryfall_id=sid)
    assert syncs(Deck(ownership="full"), dc()) is True
    assert syncs(Deck(ownership="partial"), dc()) is True
    assert syncs(Deck(ownership="none"), dc()) is False           # unowned deck
    assert syncs(Deck(ownership="full"), dc(owned=False)) is False  # unowned card
    assert syncs(Deck(ownership="full"), dc(sid=None)) is False     # unmatched line


# --- import sets ownership + owned flags -------------------------------------

@pytest.mark.asyncio
async def test_full_import_marks_owned_and_adds_to_collection(client, session):
    sol = await _add_card(session, _raw("Sol Ring"))
    resp = await client.post("/decks", data={"name": "D", "decklist": "2 Sol Ring\n1 Bogus",
                                             "ownership": "full"}, follow_redirects=False)
    deck_id = int(resp.headers["location"].rsplit("/", 1)[1])
    deck = await session.get(Deck, deck_id)
    assert deck.ownership == "full"
    by_name = {c.name: c for c in deck.cards}
    assert by_name["Sol Ring"].owned is True
    assert by_name["Bogus"].owned is False        # unmatched -> not owned
    assert await _owned_qty(session, sol) == 2


@pytest.mark.asyncio
async def test_partial_import_marks_only_checked(client, session):
    sol = await _add_card(session, _raw("Sol Ring"))
    bear = await _add_card(session, _raw("Grizzly Bears"))
    resp = await client.post(
        "/decks/owned-confirm",
        data={"name": "D", "decklist": "1 Sol Ring\n1 Grizzly Bears",
              "owned": [f"{sol.scryfall_id}|1"]},   # only Sol Ring ticked
        follow_redirects=False)
    deck_id = int(resp.headers["location"].rsplit("/", 1)[1])
    deck = await session.get(Deck, deck_id)
    assert deck.ownership == "partial"
    by_name = {c.name: c for c in deck.cards}
    assert by_name["Sol Ring"].owned is True and by_name["Grizzly Bears"].owned is False
    assert await _owned_qty(session, sol) == 1 and await _owned_qty(session, bear) == 0


# --- quantity route syncs ----------------------------------------------------

async def _owned_deck(client, session, card, qty=2):
    resp = await client.post("/decks", data={"name": "D", "decklist": f"{qty} {card.name}",
                                             "ownership": "full"}, follow_redirects=False)
    deck_id = int(resp.headers["location"].rsplit("/", 1)[1])
    deck = await session.get(Deck, deck_id)
    return deck, deck.cards[0]


@pytest.mark.asyncio
async def test_qty_route_syncs_collection(client, session):
    sol = await _add_card(session, _raw("Sol Ring"))
    deck, dc = await _owned_deck(client, session, sol, qty=2)
    assert await _owned_qty(session, sol) == 2

    await client.post(f"/decks/{deck.id}/card/{dc.id}/qty", data={"delta": "1"})
    await session.refresh(dc)
    assert dc.quantity == 3 and await _owned_qty(session, sol) == 3   # +1 mirrored

    await client.post(f"/decks/{deck.id}/card/{dc.id}/qty", data={"delta": "-1"})
    await session.refresh(dc)
    assert dc.quantity == 2 and await _owned_qty(session, sol) == 2   # -1 mirrored

    # Flooring at 1 doesn't over-decrement the collection.
    dc.quantity = 1
    await session.commit()
    coll_before = await _owned_qty(session, sol)
    await client.post(f"/decks/{deck.id}/card/{dc.id}/qty", data={"delta": "-1"})
    await session.refresh(dc)
    assert dc.quantity == 1 and await _owned_qty(session, sol) == coll_before


@pytest.mark.asyncio
async def test_qty_route_no_sync_for_unowned_deck(client, session):
    sol = await _add_card(session, _raw("Sol Ring"))
    resp = await client.post("/decks", data={"name": "D", "decklist": "1 Sol Ring",
                                             "ownership": "unowned"}, follow_redirects=False)
    deck = await session.get(Deck, int(resp.headers["location"].rsplit("/", 1)[1]))
    dc = deck.cards[0]
    await client.post(f"/decks/{deck.id}/card/{dc.id}/qty", data={"delta": "1"})
    await session.refresh(dc)
    # deck changed, collection didn't
    assert dc.quantity == 2 and await _owned_qty(session, sol) == 0


# --- printing route syncs ----------------------------------------------------

@pytest.mark.asyncio
async def test_printing_change_moves_collection(client, session):
    # Two printings of the same card (shared oracle); own it via a full-owned deck on printing A.
    oracle = str(uuid.uuid4())
    a = {**_raw("Reprint Me"), "id": str(uuid.uuid4()), "oracle_id": oracle, "set": "aaa"}
    b = {**_raw("Reprint Me"), "id": str(uuid.uuid4()), "oracle_id": oracle, "set": "bbb"}
    ca, cb = await _add_card(session, a), await _add_card(session, b)
    deck, dc = await _owned_deck(client, session, ca, qty=2)
    assert await _owned_qty(session, ca) == 2

    resp = await client.post(f"/decks/{deck.id}/card/{dc.id}",
                             data={"scryfall_id": str(cb.scryfall_id), "language": "en"})
    assert resp.status_code == 204
    assert await _owned_qty(session, ca) == 0 and await _owned_qty(session, cb) == 2  # moved


@pytest.mark.asyncio
async def test_qty_route_read_only(client, session, monkeypatch):
    sol = await _add_card(session, _raw("Sol Ring"))
    deck, dc = await _owned_deck(client, session, sol)
    monkeypatch.setattr(get_settings(), "read_only", True)
    assert (await client.post(f"/decks/{deck.id}/card/{dc.id}/qty",
                              data={"delta": "1"})).status_code == 403
