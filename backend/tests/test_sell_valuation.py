"""Sell list + valuation report (#97)."""

import uuid

import pytest
from src.models import Card, CollectionCard
from src.scryfall.mapping import card_to_columns
from src.sell import sell_list
from src.valuation import valuation_report


async def _own(session, name, usd, *, rarity="rare", set_code="tst", finish="normal",
               qty=1, tags=None):
    c = Card(**card_to_columns(
        {"id": str(uuid.uuid4()), "oracle_id": str(uuid.uuid4()), "name": name,
         "set": set_code, "collector_number": str(abs(hash(name)) % 9999), "rarity": rarity,
         "prices": {"usd": usd}}
    ))
    session.add(c)
    await session.flush()
    session.add(CollectionCard(scryfall_id=c.scryfall_id, quantity=qty, finish=finish, tags=tags))
    await session.commit()
    return c


@pytest.mark.asyncio
async def test_sell_list_only_flagged(session):
    await _own(session, "Sol Ring", "5.00", tags=["for-sale"], qty=2)
    await _own(session, "Cheap Card", "0.10", tags=["for sale"])   # alt spelling
    await _own(session, "Keeper", "9.00")                          # not flagged

    sl = await sell_list(session, "usd")
    names = [c.name for c in sl.cards]
    assert "Sol Ring" in names and "Cheap Card" in names
    assert "Keeper" not in names
    # 2 x $5 + 1 x $0.10
    assert sl.total_cards == 3
    assert sl.total_value == pytest.approx(10.10)
    # most valuable first
    assert sl.cards[0].name == "Sol Ring" and sl.cards[0].value == pytest.approx(10.00)


@pytest.mark.asyncio
async def test_valuation_report(session):
    await _own(session, "Mythic A", "20.00", rarity="mythic", set_code="aaa")
    await _own(session, "Rare B", "5.00", rarity="rare", set_code="aaa", qty=2)
    await _own(session, "Common C", "0.05", rarity="common", set_code="bbb")

    r = await valuation_report(session, "usd")
    assert r.total_cards == 4
    assert r.total_value == pytest.approx(30.05)   # 20 + 2*5 + 0.05
    assert r.distinct_cards == 3
    rarity = {row.label: row.value for row in r.by_rarity}
    assert rarity["mythic"] == pytest.approx(20.00) and rarity["rare"] == pytest.approx(10.00)
    # sets ordered by value: aaa (30) before bbb (0.05)
    assert r.by_set[0].label.lower().startswith("aaa") or r.by_set[0].value == pytest.approx(30.00)
    assert r.top_cards[0].name == "Mythic A"


@pytest.mark.asyncio
async def test_sell_routes(client, session):
    await _own(session, "Sol Ring", "5.00", tags=["for-sale"])

    tab = await client.get("/collection?tab=sell")
    assert tab.status_code == 200 and "Sol Ring" in tab.text

    redirect = await client.get("/sell", follow_redirects=False)
    assert redirect.status_code == 307 and redirect.headers["location"] == "/collection?tab=sell"

    csv_resp = await client.get("/sell/export?fmt=csv")
    assert csv_resp.status_code == 200
    assert "Sol Ring" in csv_resp.text and "TOTAL" in csv_resp.text

    val = await client.get("/valuation")
    assert val.status_code == 200 and "Collection valuation" in val.text
