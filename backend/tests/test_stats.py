"""Collection stats tests: aggregation correctness + the dashboard route."""

import datetime
import uuid

import pytest
from src.models import Card, CollectionCard
from src.scryfall.mapping import card_to_columns
from src.stats import collection_growth, collection_stats


async def _seed(session):
    cards = [
        # name, set, rarity, cmc, type, colors/identity, usd, usd_foil
        ("Aaa", "common", 2, "Creature — Bear", ["W"], "0.10", None, 2, "normal"),
        ("Bbb", "rare", 3, "Instant", ["U"], "5.00", "12.00", 1, "foil"),
        ("Ccc", "mythic", 6, "Sorcery", ["U", "R"], "3.00", None, 3, "normal"),
        ("Ddd", "uncommon", 0, "Artifact", [], "1.00", None, 1, "normal"),
    ]
    for i, (name, rarity, cmc, tl, ci, usd, usd_foil, qty, finish) in enumerate(cards):
        prices = {"usd": usd}
        if usd_foil:
            prices["usd_foil"] = usd_foil
        raw = {"id": str(uuid.uuid4()), "oracle_id": str(uuid.uuid4()), "name": name,
               "set": "TST", "collector_number": str(i), "rarity": rarity, "cmc": cmc,
               "type_line": tl, "colors": ci, "color_identity": ci, "prices": prices}
        card = Card(**card_to_columns(raw))
        session.add(card)
        await session.flush()
        session.add(CollectionCard(scryfall_id=card.scryfall_id, quantity=qty, finish=finish))
    await session.commit()


@pytest.mark.asyncio
async def test_totals_and_value(session):
    await _seed(session)
    s = await collection_stats(session)
    assert s.total_cards == 7          # 2 + 1 + 3 + 1
    assert s.printings == 4
    assert s.distinct_cards == 4
    # 2*0.10 + 1*12.00 (foil) + 3*3.00 + 1*1.00
    assert round(s.total_value, 2) == 22.20


@pytest.mark.asyncio
async def test_breakdowns(session):
    await _seed(session)
    s = await collection_stats(session)
    colors = {b.label: b.count for b in s.by_color}
    assert colors == {"White": 2, "Blue": 1, "Multicolor": 3, "Colorless": 1}
    rarity = {b.label: b.count for b in s.by_rarity}
    assert rarity == {"common": 2, "rare": 1, "mythic": 3, "uncommon": 1}
    # Rarity bars are ordered common < uncommon < rare < mythic.
    assert [b.label for b in s.by_rarity] == ["common", "uncommon", "rare", "mythic"]
    types = {b.label: b.count for b in s.by_type}
    assert types == {"Creature": 2, "Instant": 1, "Sorcery": 3, "Artifact": 1}


@pytest.mark.asyncio
async def test_most_valuable_order(session):
    await _seed(session)
    s = await collection_stats(session)
    assert [v.name for v in s.most_valuable] == ["Bbb", "Ccc", "Ddd", "Aaa"]
    assert round(s.most_valuable[0].usd, 2) == 12.00  # foil price used


@pytest.mark.asyncio
async def test_empty_collection(session):
    s = await collection_stats(session)
    assert s.is_empty and s.total_cards == 0


@pytest.mark.asyncio
async def test_stats_route_renders(client, session):
    await _seed(session)
    resp = await client.get("/stats")
    assert resp.status_code == 200
    assert "Collection stats" in resp.text
    assert "Most valuable" in resp.text
    assert "$22.20" in resp.text
    assert "ms ms-cost ms-" in resp.text  # color breakdown shows mana pips


@pytest.mark.asyncio
async def test_advanced_page_uses_mana_pips(client):
    resp = await client.get("/advanced")
    assert resp.status_code == 200
    # Color picker renders mana pips (the class is bound by Alpine from the letter).
    assert "ms ms-cost" in resp.text
    assert "'ms-' + c.toLowerCase()" in resp.text


@pytest.mark.asyncio
async def test_collection_growth_by_month(session):
    raw = {"id": str(uuid.uuid4()), "oracle_id": str(uuid.uuid4()), "name": "Aaa", "set": "TST",
           "collector_number": "1", "rarity": "rare", "prices": {"usd": "2.00"}}
    card = Card(**card_to_columns(raw))
    session.add(card)
    await session.flush()
    # Two stacks added in Jan, one in March (different added_at months).
    jan = datetime.datetime(2026, 1, 10, tzinfo=datetime.UTC)
    mar = datetime.datetime(2026, 3, 5, tzinfo=datetime.UTC)
    session.add(CollectionCard(scryfall_id=card.scryfall_id, quantity=3, finish="normal",
                               added_at=jan))
    session.add(CollectionCard(scryfall_id=card.scryfall_id, quantity=1, finish="foil",
                               condition="nm", added_at=jan))
    session.add(CollectionCard(scryfall_id=card.scryfall_id, quantity=2, finish="normal",
                               binder_name="B", added_at=mar))
    await session.commit()

    growth = await collection_growth(session)
    assert growth.available
    labels = {p.label: p.added for p in growth.points}
    assert labels == {"2026-01": 4, "2026-03": 2}     # 3+1 in Jan, 2 in Mar
    assert growth.total_added == 6
    assert growth.total_months == 2
    assert growth.points[0].label == "2026-01"        # oldest-first
    assert growth.points[0].value == 8.00             # 4 cards * $2.00


@pytest.mark.asyncio
async def test_collection_growth_empty(session):
    growth = await collection_growth(session)
    assert not growth.available
    assert growth.total_added == 0


@pytest.mark.asyncio
async def test_stats_route_shows_growth(client, session):
    await _seed(session)
    resp = await client.get("/stats")
    assert "Collection growth" in resp.text
