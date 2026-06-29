"""Trade binder: surplus + tagged-for-trade selection, values, and export."""

import uuid

import pytest
from src.models import Card, CollectionCard
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
    page = await client.get("/trade")
    assert page.status_code == 200
    assert "Trade binder" in page.text and "Spare" in page.text

    txt = await client.get("/trade/export?fmt=txt")
    assert txt.status_code == 200
    assert "2 Spare (TST) 1" in txt.text   # keep=1 -> 2 spare
    assert 'filename="scryme-trade.txt"' in txt.headers["content-disposition"]

    csv_resp = await client.get("/trade/export?fmt=csv")
    assert "Quantity,Name,Set" in csv_resp.text
