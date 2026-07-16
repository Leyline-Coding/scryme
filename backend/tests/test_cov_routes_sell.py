"""Coverage tests for src/routes/sell.py: sell page redirect, CSV/txt export, valuation page."""

import uuid

import pytest
from src.models import Card, CollectionCard
from src.scryfall.mapping import card_to_columns


async def _flagged(session, *, name="Sell Me", usd="12.34", n=1):
    c = Card(**card_to_columns(
        {"id": str(uuid.uuid4()), "oracle_id": str(uuid.uuid4()), "name": name,
         "set": "tst", "collector_number": str(n), "rarity": "rare",
         "prices": {"usd": usd}}))
    session.add(c)
    await session.flush()
    session.add(CollectionCard(scryfall_id=c.scryfall_id, quantity=2, tags=["for-sale"]))
    await session.commit()
    return c


@pytest.mark.asyncio
async def test_sell_page_redirects(client):
    resp = await client.get("/sell", follow_redirects=False)
    assert resp.status_code == 307
    assert resp.headers["location"] == "/collection?tab=sell"


@pytest.mark.asyncio
async def test_sell_export_csv(client, session):
    await _flagged(session)
    resp = await client.get("/sell/export")
    assert resp.status_code == 200
    assert "text/csv" in resp.headers["content-type"]
    assert "scryme-sell.csv" in resp.headers["content-disposition"]
    assert "Sell Me" in resp.text and "TOTAL" in resp.text


@pytest.mark.asyncio
async def test_sell_export_txt(client, session):
    await _flagged(session, name="Text Card")
    resp = await client.get("/sell/export?fmt=txt")
    assert resp.status_code == 200
    assert "scryme-sell.txt" in resp.headers["content-disposition"]
    assert "Text Card" in resp.text


@pytest.mark.asyncio
async def test_sell_export_txt_empty(client, session):
    resp = await client.get("/sell/export?fmt=txt")
    assert resp.status_code == 200
    assert resp.text == ""


@pytest.mark.asyncio
async def test_valuation_page(client, session):
    await _flagged(session)
    resp = await client.get("/valuation")
    assert resp.status_code == 200
