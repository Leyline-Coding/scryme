"""JSON API (/api/v1): read endpoints, mutations, read-only + token guards."""

import uuid

import pytest
from src.models import Card, CollectionCard
from src.scryfall.mapping import card_to_columns


async def _card(session, name="Aaa", n=1, owned=0, oracle=None):
    raw = {"id": str(uuid.uuid4()), "oracle_id": oracle or str(uuid.uuid4()), "name": name,
           "set": "tst", "collector_number": str(n), "rarity": "rare", "type_line": "Instant",
           "colors": ["R"], "color_identity": ["R"], "prices": {"usd": "2.00", "eur": "1.50"},
           "image_uris": {"normal": "http://img/x.jpg"}}
    c = Card(**card_to_columns(raw))
    session.add(c)
    await session.flush()
    if owned:
        session.add(CollectionCard(scryfall_id=c.scryfall_id, quantity=owned))
    await session.commit()
    return c


@pytest.mark.asyncio
async def test_api_search(client, session):
    await _card(session, "Bolt", 1, owned=3)
    resp = await client.get("/api/v1/search?q=bolt&scope=all")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1 and data["page"] == 1
    card = data["cards"][0]
    assert card["name"] == "Bolt" and card["quantity"] == 3
    assert card["image"] == "http://img/x.jpg" and card["prices"]["usd"] == "2.00"


@pytest.mark.asyncio
async def test_api_search_bad_query(client, session):
    resp = await client.get("/api/v1/search?q=" + "badfield:x")
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_api_card_detail(client, session):
    c = await _card(session, "Bolt", 1, owned=2)
    resp = await client.get(f"/api/v1/cards/{c.scryfall_id}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == "Bolt"
    assert data["oracle_id"] is not None
    assert len(data["owned"]) == 1 and data["owned"][0]["quantity"] == 2
    missing = await client.get("/api/v1/cards/00000000-0000-0000-0000-000000000000")
    assert missing.status_code == 404


@pytest.mark.asyncio
async def test_api_stats(client, session):
    await _card(session, "Bolt", 1, owned=3)
    resp = await client.get("/api/v1/stats")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_cards"] == 3
    assert data["total_value"] == 6.00  # 3 * $2.00
    assert any(b["label"] == "Red" for b in data["by_color"])


@pytest.mark.asyncio
async def test_api_collection_add_and_card_reflects(client, session):
    c = await _card(session, "Bolt", 1)
    add = await client.post("/api/v1/collection", json={"scryfall_id": str(c.scryfall_id),
                                                        "quantity": 4, "finish": "foil"})
    assert add.status_code == 200 and add.json()["quantity"] == 4
    detail = await client.get(f"/api/v1/cards/{c.scryfall_id}")
    assert detail.json()["quantity"] == 4


@pytest.mark.asyncio
async def test_api_tags_and_wishlist(client, session):
    c = await _card(session, "Bolt", 1, owned=1)
    cid = str(c.scryfall_id)
    tagged = await client.post(f"/api/v1/cards/{cid}/tags", json={"tag": "Trade"})
    assert tagged.json()["tags"] == ["trade"]
    untagged = await client.request("DELETE", f"/api/v1/cards/{cid}/tags?tag=trade")
    assert untagged.json()["tags"] == []

    w = await client.post("/api/v1/wishlist", json={"scryfall_id": cid, "quantity": 2})
    assert w.status_code == 200
    wl = await client.get("/api/v1/wishlist")
    assert wl.json()["total_cards"] == 2 and wl.json()["items"][0]["price"] == 2.00
    rm = await client.request("DELETE", f"/api/v1/wishlist/{cid}")
    assert rm.status_code == 200
    assert (await client.get("/api/v1/wishlist")).json()["total_cards"] == 0


@pytest.mark.asyncio
async def test_api_decks(client, session):
    from src.decks import create_deck
    await _card(session, "Bolt", 1, owned=1)
    deck = await create_deck(session, "Burn", "4 Bolt")
    listing = await client.get("/api/v1/decks")
    assert any(d["name"] == "Burn" for d in listing.json())
    detail = await client.get(f"/api/v1/decks/{deck.id}")
    assert detail.status_code == 200
    assert detail.json()["total_needed"] == 4


@pytest.mark.asyncio
async def test_api_mutation_blocked_read_only(client, session, monkeypatch):
    from src.config import get_settings
    monkeypatch.setattr(get_settings(), "read_only", True)
    c = await _card(session, "Bolt", 1)
    resp = await client.post("/api/v1/collection", json={"scryfall_id": str(c.scryfall_id)})
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_api_token_required_when_configured(client, session, monkeypatch):
    from src.config import get_settings
    monkeypatch.setattr(get_settings(), "api_token", "secret")
    await _card(session, "Bolt", 1, owned=1)
    assert (await client.get("/api/v1/stats")).status_code == 401
    ok = await client.get("/api/v1/stats", headers={"Authorization": "Bearer secret"})
    assert ok.status_code == 200
    ok2 = await client.get("/api/v1/stats", headers={"X-API-Key": "secret"})
    assert ok2.status_code == 200
