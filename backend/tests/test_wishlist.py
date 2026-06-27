"""Wishlist: add/remove/bump, deck-missing import, cost totals, and routes."""

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


async def _add_card(session, raw):
    card = Card(**card_to_columns(raw))
    session.add(card)
    await session.commit()
    return card


@pytest.mark.asyncio
async def test_add_bump_and_remove(session):
    card = await _add_card(session, _raw("Aaa", 1, usd="2.50"))
    item = await add_to_wishlist(session, card.scryfall_id, 1)
    assert item is not None and item.quantity == 1
    assert await is_wishlisted(session, card.scryfall_id)

    # Re-adding raises to the larger quantity (idempotent for deck imports), not additive.
    bumped = await add_to_wishlist(session, card.scryfall_id, 3)
    assert bumped.quantity == 3
    again = await add_to_wishlist(session, card.scryfall_id, 2)
    assert again.quantity == 3

    await remove_from_wishlist(session, card.scryfall_id)
    assert not await is_wishlisted(session, card.scryfall_id)


@pytest.mark.asyncio
async def test_add_unknown_card_is_noop(session):
    assert await add_to_wishlist(session, uuid.uuid4(), 1) is None


@pytest.mark.asyncio
async def test_list_totals(session):
    a = await _add_card(session, _raw("Aaa", 1, usd="2.00"))
    b = await _add_card(session, _raw("Bbb", 2, usd="5.00"))
    await add_to_wishlist(session, a.scryfall_id, 2)
    await add_to_wishlist(session, b.scryfall_id, 1)
    view = await list_wishlist(session)
    assert view.total_cards == 3
    assert view.total_cost == 9.00  # 2*2.00 + 1*5.00


@pytest.mark.asyncio
async def test_add_deck_missing_only_unowned(session):
    oracle_owned = str(uuid.uuid4())
    owned = await _add_card(session, _raw("Owned", 1, oracle=oracle_owned))
    await _add_card(session, _raw("Wanted", 2, oracle=str(uuid.uuid4())))
    # Own one copy of "Owned".
    session.add(CollectionCard(scryfall_id=owned.scryfall_id, quantity=1, finish="normal"))
    await session.commit()

    deck = await create_deck(session, "Test", "2 Owned\n3 Wanted")
    added = await add_deck_missing(session, deck)
    assert added == 2  # Owned (need 2, have 1) + Wanted (need 3, have 0)

    view = await list_wishlist(session)
    by_name = {item.card.name: item.quantity for item in view.items}
    assert by_name == {"Owned": 1, "Wanted": 3}  # only the missing counts
    assert all(item.note == "for Test" for item in view.items)


@pytest.mark.asyncio
async def test_wishlist_routes(client, session):
    card = await _add_card(session, _raw("Aaa", 1))
    cid = str(card.scryfall_id)

    add = await client.post("/wishlist/add", data={"scryfall_id": cid})
    assert add.status_code == 200
    assert "On wishlist" in add.text

    page = await client.get("/wishlist")
    assert page.status_code == 200
    assert "Aaa" in page.text

    rm = await client.post("/wishlist/remove", data={"scryfall_id": cid},
                           headers={"HX-Request": "true"})
    assert rm.status_code == 200
    assert "Add to wishlist" in rm.text


@pytest.mark.asyncio
async def test_read_only_blocks_wishlist(client, session, monkeypatch):
    from src.config import get_settings
    monkeypatch.setattr(get_settings(), "read_only", True)
    card = await _add_card(session, _raw("Aaa", 1))
    resp = await client.post("/wishlist/add", data={"scryfall_id": str(card.scryfall_id)})
    assert resp.status_code == 403
