"""Coverage tests for src/sell.py: sell_list flagging, valuation, totals."""

import uuid

import pytest
from src.models import Card, CollectionCard
from src.scryfall.mapping import card_to_columns
from src.sell import SellCard, SellList, sell_list


async def _own(session, *, name="Sell Me", usd="10.00", qty=2, tags=None, n=1):
    c = Card(**card_to_columns(
        {"id": str(uuid.uuid4()), "oracle_id": str(uuid.uuid4()), "name": name,
         "set": "tst", "collector_number": str(n), "rarity": "rare",
         "prices": {"usd": usd}}))
    session.add(c)
    await session.flush()
    session.add(CollectionCard(scryfall_id=c.scryfall_id, quantity=qty, tags=tags))
    await session.commit()
    return c


def test_sellcard_value_and_totals():
    a = SellCard("i", "A", "tst", "Test", "1", "rare", 3, 2.5)
    b = SellCard("j", "B", "tst", "Test", "2", "rare", 1, 4.0)
    sl = SellList(cards=[a, b])
    assert a.value == 7.5
    assert sl.total_value == 11.5
    assert sl.total_cards == 4


@pytest.mark.asyncio
async def test_sell_list_only_flagged_sorted(session):
    await _own(session, name="Cheap", usd="1.00", tags=["for-sale"], n=1)
    await _own(session, name="Pricey", usd="50.00", tags=["sale"], n=2)
    await _own(session, name="Keep", usd="99.00", tags=None, n=3)  # not flagged
    sl = await sell_list(session, "usd")
    names = [c.name for c in sl.cards]
    assert names == ["Pricey", "Cheap"]  # most valuable first, Keep excluded
    assert "Keep" not in names


@pytest.mark.asyncio
async def test_sell_list_empty(session):
    sl = await sell_list(session)
    assert sl.cards == [] and sl.total_value == 0 and sl.total_cards == 0
