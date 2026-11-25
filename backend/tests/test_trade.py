"""Trade binder: surplus + tagged-for-trade selection, values, and export."""

import uuid

import pytest
from src.models import Card, CollectionCard, Location
from src.scryfall.mapping import card_to_columns
from src.trade import trade_binder


async def _own(session, name, n, qty, finish="normal", tags=None, usd="1.00"):
    raw = {"id": str(uuid.uuid4()), "oracle_id": str(uuid.uuid4()), "name": name, "set": "tst",
           "collector_number": str(n), "rarity": "rare", "prices": {"usd": usd}}
    c = Card(**card_to_columns(raw))
    session.add(c)
    await session.flush()
    session.add(CollectionCard(scryfall_id=c.scryfall_id, quantity=qty, finish=finish, tags=tags))
    await session.commit()
    return c


@pytest.mark.asyncio
async def test_surplus_beyond_keep(session):
    await _own(session, "Spare", 1, qty=5, usd="2.00")   # 5 owned, keep 1 -> 4 tradeable
    await _own(session, "Single", 2, qty=1)              # 1 owned -> nothing to trade
    binder = await trade_binder(session, "usd", keep=1)
    names = {c.name: c for c in binder.cards}
    assert "Single" not in names
    spare = names["Spare"]
    assert spare.owned == 5 and spare.tradeable == 4
    assert spare.value == 8.00          # 4 * $2.00
    assert binder.total_cards == 4 and binder.total_value == 8.00


@pytest.mark.asyncio
async def test_keep_threshold(session):
    await _own(session, "Playset", 1, qty=4, usd="1.00")
    # keep=4 -> nothing tradeable; keep=1 -> 3 tradeable.
    assert (await trade_binder(session, "usd", keep=4)).cards == []
    binder = await trade_binder(session, "usd", keep=1)
    assert binder.cards[0].tradeable == 3


@pytest.mark.asyncio
async def test_for_trade_tag_included_regardless_of_quantity(session):
    await _own(session, "Flagged", 1, qty=1, tags=["for-trade"])
    binder = await trade_binder(session, "usd", keep=1)
    card = binder.cards[0]
    assert card.name == "Flagged" and card.for_trade and card.tradeable == 1


@pytest.mark.asyncio
async def test_trade_routes(client, session):
    await _own(session, "Spare", 1, qty=3, usd="2.50")
    page = await client.get("/collection/trade")
    assert page.status_code == 200
    assert "My Collection" in page.text and "Spare" in page.text

    txt = await client.get("/trade/export?fmt=txt")
    assert txt.status_code == 200
    assert "2 Spare (TST) 1" in txt.text   # keep=1 -> 2 spare
    assert 'filename="scryme-trade.txt"' in txt.headers["content-disposition"]

    csv_resp = await client.get("/trade/export?fmt=csv")
    assert "Quantity,Name,Set" in csv_resp.text


async def _put_in_deck(session, card, qty, tags=None):
    """A second stack of `card` that physically sits in a deck."""
    loc = Location(type="deck", foreign_id=1)
    session.add(loc)
    await session.flush()
    session.add(CollectionCard(scryfall_id=card.scryfall_id, quantity=qty, location=loc.id,
                               tags=tags))
    await session.commit()


@pytest.mark.asyncio
async def test_exclude_decks_ignores_copies_in_a_deck(session):
    """#408: a copy sleeved up in a deck is not a spare."""
    card = await _own(session, "Staple", 1, qty=2)      # 2 loose
    await _put_in_deck(session, card, qty=2)            # 2 in a deck
    only_in_deck = await _own(session, "Decked", 2, qty=0)
    await _put_in_deck(session, only_in_deck, qty=3)

    counted = {c.name: c for c in (await trade_binder(session, "usd", keep=1)).cards}
    assert counted["Staple"].tradeable == 3 and counted["Decked"].tradeable == 2

    binder = await trade_binder(session, "usd", keep=1, exclude_decks=True)
    assert binder.exclude_decks
    spared = {c.name: c for c in binder.cards}
    assert spared["Staple"].owned == 2 and spared["Staple"].tradeable == 1
    assert "Decked" not in spared


@pytest.mark.asyncio
async def test_exclude_decks_keeps_non_deck_locations_and_trade_tags(session):
    boxed = await _own(session, "Boxed", 1, qty=1)
    box = Location(type="box", foreign_id=1)
    session.add(box)
    await session.flush()
    session.add(CollectionCard(scryfall_id=boxed.scryfall_id, quantity=2, location=box.id))
    flagged = await _own(session, "Flagged", 2, qty=0)
    await _put_in_deck(session, flagged, qty=1, tags=["for-trade"])

    binder = await trade_binder(session, "usd", keep=1, exclude_decks=True)
    cards = {c.name: c for c in binder.cards}
    assert cards["Boxed"].tradeable == 2           # a box is not a deck
    assert cards["Flagged"].tradeable == 1         # tagged for trade wins, even in a deck


@pytest.mark.asyncio
async def test_exclude_decks_routes(client, session):
    card = await _own(session, "Staple", 1, qty=1)
    await _put_in_deck(session, card, qty=1)

    page = await client.get("/collection/trade")
    assert "Staple" in page.text and "exclude_decks=1" not in page.text
    page = await client.get("/collection/trade?exclude_decks=1")
    assert page.status_code == 200 and "Staple" not in page.text

    assert "Staple" in (await client.get("/trade/export?fmt=txt")).text
    assert "Staple" not in (await client.get("/trade/export?fmt=txt&exclude_decks=1")).text
    redirect = await client.get("/trade?keep=2&exclude_decks=1", follow_redirects=False)
    assert redirect.headers["location"] == "/collection/trade?keep=2&exclude_decks=1"
