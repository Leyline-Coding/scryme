"""Preferred-marketplace pricing (#231): parsers, source resolution/fallback, and display."""

import uuid

import pytest
from src.market_prices import cardkingdom_price, manapool_price
from src.models import Card, CollectionCard
from src.pricing import available_sources, effective_prices, resolve_prices
from src.scryfall.mapping import card_to_columns
from src.stats import collection_stats


# --- pure parsers ---------------------------------------------------------------------------

def test_cardkingdom_parser_takes_latest_date():
    obj = {"paper": {"cardkingdom": {"retail": {
        "normal": {"2026-07-14": 1.0, "2026-07-15": 1.5},
        "foil": {"2026-07-15": 9.99},
    }}}}
    assert cardkingdom_price(obj) == {"usd": "1.50", "usd_foil": "9.99"}
    assert cardkingdom_price({}) is None
    assert cardkingdom_price({"paper": {"cardkingdom": {"retail": {}}}}) is None


def test_manapool_parser_prefers_market_price_and_converts_cents():
    row = {"price_market": 184, "price_cents": 262, "price_market_foil": 2500,
           "price_cents_foil": 2703, "price_cents_etched": 999}
    assert manapool_price(row) == {"usd": "1.84", "usd_foil": "25.00", "usd_etched": "9.99"}
    # Falls back to the lowest listing when there's no market price.
    assert manapool_price({"price_cents": 262}) == {"usd": "2.62"}
    assert manapool_price({"price_cents": None}) is None


# --- resolution + fallback ------------------------------------------------------------------

def test_resolve_prices_source_override_and_fallback():
    prices = {"usd": "1.00", "usd_foil": "2.00", "eur": "0.90"}
    market = {"cardkingdom": {"usd": "5.00"}, "manapool": {"usd": "3.00", "usd_foil": "6.00"}}
    # TCGplayer = untouched Scryfall.
    assert resolve_prices(prices, market, "tcgplayer") == prices
    # Card Kingdom overrides usd, keeps Scryfall usd_foil (CK had none) + eur.
    ck = resolve_prices(prices, market, "cardkingdom")
    assert ck["usd"] == "5.00" and ck["usd_foil"] == "2.00" and ck["eur"] == "0.90"
    # ManaPool overrides both usd keys.
    mp = resolve_prices(prices, market, "manapool")
    assert mp["usd"] == "3.00" and mp["usd_foil"] == "6.00"
    # A source with no data for this card falls back to Scryfall.
    assert resolve_prices(prices, {}, "cardkingdom") == prices


def test_effective_prices_on_card_object():
    card = Card(**card_to_columns({
        "id": str(uuid.uuid4()), "name": "X", "set": "tst", "collector_number": "1",
        "prices": {"usd": "1.00"},
    }))
    card.market_prices = {"cardkingdom": {"usd": "7.77"}}
    assert effective_prices(card, "cardkingdom")["usd"] == "7.77"
    assert effective_prices(card, "tcgplayer")["usd"] == "1.00"


# --- integration ----------------------------------------------------------------------------

async def _seed_owned(session, prices, market_prices):
    raw = {"id": str(uuid.uuid4()), "name": "Priced Card", "set": "tst", "collector_number": "1",
           "rarity": "rare", "type_line": "Artifact", "prices": prices}
    c = Card(**card_to_columns(raw))
    c.market_prices = market_prices
    session.add(c)
    await session.flush()
    session.add(CollectionCard(scryfall_id=c.scryfall_id, quantity=2, finish="normal"))
    await session.commit()
    return c


@pytest.mark.asyncio
async def test_stats_value_reflects_source(session):
    await _seed_owned(session, {"usd": "1.00"}, {"cardkingdom": {"usd": "10.00"}})
    tcg = await collection_stats(session, "usd", "tcgplayer")
    ck = await collection_stats(session, "usd", "cardkingdom")
    assert round(tcg.total_value, 2) == 2.00      # 2 x $1 (Scryfall)
    assert round(ck.total_value, 2) == 20.00      # 2 x $10 (Card Kingdom)


@pytest.mark.asyncio
async def test_available_sources_reports_synced(session):
    assert await available_sources(session) == ["tcgplayer"]
    await _seed_owned(session, {"usd": "1.00"}, {"manapool": {"usd": "2.00"}})
    got = await available_sources(session)
    assert "tcgplayer" in got and "manapool" in got and "cardkingdom" not in got


@pytest.mark.asyncio
async def test_card_page_shows_selected_source_price(client, session):
    card = await _seed_owned(session, {"usd": "1.00"}, {"cardkingdom": {"usd": "42.00"}})
    resp = await client.get(f"/card/{card.scryfall_id}",
                            headers={"Cookie": "scryme_price_source=cardkingdom"})
    assert resp.status_code == 200
    assert "42.00" in resp.text and "Card Kingdom" in resp.text
