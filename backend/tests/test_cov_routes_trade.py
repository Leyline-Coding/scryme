"""Coverage tests for src.routes.trade: the tab redirect and the txt/csv exports."""

import uuid

import pytest
from src.models import Card, CollectionCard
from src.routes import trade as R
from src.scryfall.mapping import card_to_columns


async def _own(session, name, n, qty, usd="2.50"):
    c = Card(**card_to_columns(
        {"id": str(uuid.uuid4()), "oracle_id": str(uuid.uuid4()), "name": name, "set": "tst",
         "collector_number": str(n), "rarity": "rare", "prices": {"usd": usd}}
    ))
    session.add(c)
    await session.flush()
    session.add(CollectionCard(scryfall_id=c.scryfall_id, quantity=qty))
    await session.commit()
    return c


@pytest.mark.asyncio
async def test_trade_page_redirect():
    resp = await R.trade_page(keep=2)
    assert resp.status_code == 307
    assert resp.headers["location"] == "/collection?tab=trade&keep=2"


@pytest.mark.asyncio
async def test_trade_export_txt_and_csv(session):
    await _own(session, "Spare", 1, qty=3, usd="2.50")  # keep=1 -> 2 spare

    txt = await R.trade_export(fmt="txt", keep=1, session=session)
    assert txt.status_code == 200
    assert "2 Spare (TST) 1" in txt.body.decode()
    assert 'filename="scryme-trade.txt"' in txt.headers["content-disposition"]

    csv_resp = await R.trade_export(fmt="csv", keep=1, session=session)
    text = "".join([chunk async for chunk in csv_resp.body_iterator])
    assert "Quantity,Name,Set" in text and "Spare" in text
    assert 'filename="scryme-trade.csv"' in csv_resp.headers["content-disposition"]


@pytest.mark.asyncio
async def test_trade_export_empty_txt(session):
    # No tradeable cards -> empty body, no trailing newline.
    txt = await R.trade_export(fmt="txt", keep=1, session=session)
    assert txt.status_code == 200 and txt.body == b""
