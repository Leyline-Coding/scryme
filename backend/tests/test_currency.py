"""Multi-currency: price-key selection, cookie resolution, and currency-aware totals."""

import uuid

import pytest
from src.currency import info, normalize, unit_price
from src.models import Card, CollectionCard
from src.scryfall.mapping import card_to_columns
from src.stats import collection_stats

_PRICES = {"usd": "1.00", "usd_foil": "3.00", "eur": "0.80", "eur_foil": "2.50"}


def test_normalize_and_info():
    assert normalize("USD") == "usd"
    assert normalize("eur") == "eur"
    assert normalize("gbp") is None
    assert normalize(None) is None
    assert info("eur")["symbol"] == "€"
    assert info("nonsense")["symbol"] == "$"  # falls back to USD


def test_unit_price_by_currency_and_finish():
    assert unit_price(_PRICES, "normal", "usd") == 1.00
    assert unit_price(_PRICES, "foil", "usd") == 3.00
    assert unit_price(_PRICES, "normal", "eur") == 0.80
    assert unit_price(_PRICES, "etched", "eur") == 2.50
    # Foil price missing -> fall back to the base price in that currency.
    assert unit_price({"eur": "0.80"}, "foil", "eur") == 0.80
    # Unknown currency normalizes to USD; no price -> 0.
    assert unit_price(_PRICES, "normal", "gbp") == 1.00
    assert unit_price({}, "normal", "usd") == 0.0


async def _own(session, prices, qty=1, finish="normal"):
    raw = {"id": str(uuid.uuid4()), "oracle_id": str(uuid.uuid4()), "name": "Aaa", "set": "tst",
           "collector_number": "1", "rarity": "rare", "prices": prices}
    c = Card(**card_to_columns(raw))
    session.add(c)
    await session.flush()
    session.add(CollectionCard(scryfall_id=c.scryfall_id, quantity=qty, finish=finish))
    await session.commit()
    return c


@pytest.mark.asyncio
async def test_collection_stats_uses_currency(session):
    await _own(session, _PRICES, qty=2, finish="normal")
    usd = await collection_stats(session, "usd")
    eur = await collection_stats(session, "eur")
    assert usd.total_value == 2.00   # 2 * $1.00
    assert eur.total_value == 1.60   # 2 * €0.80


@pytest.mark.asyncio
async def test_stats_route_currency_cookie(client, session):
    await _own(session, _PRICES, qty=2)
    usd = await client.get("/stats")
    assert "Est. value (USD)" in usd.text and "$2.00" in usd.text

    eur = await client.get("/stats", headers={"Cookie": "scryme_currency=eur"})
    assert "Est. value (EUR)" in eur.text and "€1.60" in eur.text
