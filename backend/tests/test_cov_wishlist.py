"""Coverage tests for src.wishlist: add/bump/remove, note update, totals, deck-missing import."""

import uuid

import pytest
from src.decks import create_deck
from src.models import Card, CollectionCard
from src.scryfall.mapping import card_to_columns
from src.wishlist import (
    add_deck_missing,
    add_to_wishlist,
    is_wishlisted,
    list_wishlist,
    remove_from_wishlist,
)


def _raw(name, n, oracle=None, usd="1.00"):
    return {"id": str(uuid.uuid4()), "oracle_id": oracle or str(uuid.uuid4()), "name": name,
            "set": "TST", "collector_number": str(n), "rarity": "rare", "prices": {"usd": usd}}


async def _add(session, raw):
    card = Card(**card_to_columns(raw))
    session.add(card)
    await session.commit()
    return card


@pytest.mark.asyncio
async def test_add_bump_note_update_and_remove(session):
    card = await _add(session, _raw("Aaa", 1, usd="2.50"))
    item = await add_to_wishlist(session, card.scryfall_id, 1)
    assert item.quantity == 1 and item.note is None
    assert await is_wishlisted(session, card.scryfall_id)

    # Bump to a larger quantity and attach a note (covers the note-update branch).
    bumped = await add_to_wishlist(session, card.scryfall_id, 3, note="want foil")
    assert bumped.quantity == 3 and bumped.note == "want foil"
    # A smaller re-add keeps the larger quantity.
    assert (await add_to_wishlist(session, card.scryfall_id, 2)).quantity == 3

    await remove_from_wishlist(session, card.scryfall_id)
    assert not await is_wishlisted(session, card.scryfall_id)


@pytest.mark.asyncio
async def test_add_unknown_card_is_noop(session):
    assert await add_to_wishlist(session, uuid.uuid4(), 1) is None


@pytest.mark.asyncio
async def test_list_totals(session):
    a = await _add(session, _raw("Aaa", 1, usd="2.00"))
    b = await _add(session, _raw("Bbb", 2, usd="5.00"))
    await add_to_wishlist(session, a.scryfall_id, 2)
    await add_to_wishlist(session, b.scryfall_id, 1)
    view = await list_wishlist(session)
    assert view.total_cards == 3 and view.total_cost == 9.00


@pytest.mark.asyncio
async def test_add_deck_missing(session):
    oracle_owned = str(uuid.uuid4())
    owned = await _add(session, _raw("Owned", 1, oracle=oracle_owned))
    await _add(session, _raw("Wanted", 2))
    session.add(CollectionCard(scryfall_id=owned.scryfall_id, quantity=1, finish="normal"))
    await session.commit()

    deck = await create_deck(session, "Test", "2 Owned\n3 Wanted")
    assert await add_deck_missing(session, deck) == 2
    view = await list_wishlist(session)
    by_name = {item.card.name: item.quantity for item in view.items}
    assert by_name == {"Owned": 1, "Wanted": 3}
    assert all(item.note == "for Test" for item in view.items)
