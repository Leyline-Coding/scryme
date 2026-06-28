"""Card detail route tests: page render, owned/printings, 404s, and lazy rulings."""

import uuid

import pytest
from src.models import Card, CollectionCard
from src.routes import card as card_route
from src.scryfall.mapping import card_to_columns


async def _seed(session):
    oracle = str(uuid.uuid4())
    main = {"id": str(uuid.uuid4()), "oracle_id": oracle, "name": "Lightning Bolt", "set": "MH2",
            "collector_number": "122", "rarity": "uncommon", "cmc": 1, "type_line": "Instant",
            "colors": ["R"], "color_identity": ["R"], "released_at": "2021-06-18",
            "oracle_text": "Lightning Bolt deals 3 damage to any target.",
            "prices": {"usd": "2.50"}, "legalities": {"modern": "legal", "standard": "not_legal"},
            "scryfall_uri": "https://scryfall.test/bolt", "artist": "Christopher Rush"}
    other = {"id": str(uuid.uuid4()), "oracle_id": oracle, "name": "Lightning Bolt", "set": "LEA",
             "collector_number": "161", "rarity": "common", "cmc": 1, "type_line": "Instant",
             "colors": ["R"], "color_identity": ["R"], "released_at": "1993-08-05",
             "oracle_text": "Lightning Bolt deals 3 damage to any target."}
    main_card = Card(**card_to_columns(main))
    session.add(main_card)
    session.add(Card(**card_to_columns(other)))
    await session.flush()
    session.add(CollectionCard(scryfall_id=main_card.scryfall_id, quantity=3, finish="foil"))
    await session.commit()
    return main_card


@pytest.mark.asyncio
async def test_card_page_renders(client, session):
    card = await _seed(session)
    resp = await client.get(f"/card/{card.scryfall_id}")
    assert resp.status_code == 200
    body = resp.text
    assert "Lightning Bolt" in body
    assert "deals 3 damage" in body
    assert "Christopher Rush" in body
    assert "In your collection" in body
    assert "3 total" in body  # owned quantity (now shown with the inline +/- controls)
    assert "Other printings" in body  # the LEA printing shares the oracle_id
    assert "Legalities" in body


@pytest.mark.asyncio
async def test_card_404(client):
    assert (await client.get("/card/not-a-uuid")).status_code == 404
    assert (await client.get("/card/00000000-0000-0000-0000-000000000000")).status_code == 404


@pytest.mark.asyncio
async def test_rulings_render_from_cache(client, session):
    card = await _seed(session)
    sid = str(card.scryfall_id)
    card_route._rulings_cache[sid] = [
        {"published_at": "2020-01-01", "comment": "A test ruling about timing."}
    ]
    try:
        resp = await client.get(f"/card/{sid}/rulings")
        assert resp.status_code == 200
        assert "A test ruling about timing." in resp.text
    finally:
        card_route._rulings_cache.pop(sid, None)


@pytest.mark.asyncio
async def test_rulings_missing_uri_degrades(client, session):
    # The seeded card has no rulings_uri, so the fetch path fails gracefully (no network).
    card = await _seed(session)
    card_route._rulings_cache.pop(str(card.scryfall_id), None)
    resp = await client.get(f"/card/{card.scryfall_id}/rulings")
    assert resp.status_code == 200
    assert "couldn't be loaded" in resp.text
