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


@pytest.mark.asyncio
async def test_api_deck_crud_and_export(client, session):
    await _card(session, name="Lightning Bolt", n=1, owned=1)
    created = await client.post(
        "/api/v1/decks", json={"name": "Burn", "decklist": "1 Lightning Bolt\n2 Missing Card"}
    )
    assert created.status_code == 201
    d = created.json()
    assert d["name"] == "Burn"
    deck_id = d["id"]
    row = next(r for r in d["main"] if r["name"] == "Lightning Bolt")
    assert row["set_code"] == "tst" and row["owned"] == 1 and row["card_id"] > 0

    renamed = await client.patch(f"/api/v1/decks/{deck_id}", json={"name": "Burn v2"})
    assert renamed.json()["name"] == "Burn v2"

    export = await client.get(f"/api/v1/decks/{deck_id}/export?fmt=text")
    assert export.status_code == 200 and "Lightning Bolt" in export.text

    assert (await client.delete(f"/api/v1/decks/{deck_id}")).status_code == 200
    assert (await client.get(f"/api/v1/decks/{deck_id}")).status_code == 404


@pytest.mark.asyncio
async def test_api_deck_card_edit_and_legality(client, session):
    oracle = str(uuid.uuid4())
    playable = {"id": str(uuid.uuid4()), "oracle_id": oracle, "name": "Sol Ring", "set": "cmm",
                "collector_number": "1", "type_line": "Artifact",
                "legalities": {"commander": "legal"}, "prices": {"usd": "1"}}
    variant = {"id": str(uuid.uuid4()), "oracle_id": oracle, "name": "Sol Ring", "set": "art",
               "collector_number": "2", "type_line": "Artifact",
               "legalities": {"commander": "not_legal"}, "prices": {"usd": "1"}}
    for raw in (playable, variant):
        session.add(Card(**card_to_columns(raw)))
    await session.commit()

    deck = (await client.post("/api/v1/decks", json={"name": "C", "decklist": "1 Sol Ring"})).json()
    # Resolved to the tournament-legal printing, and legal in commander (judged by oracle).
    detail = (await client.get(f"/api/v1/decks/{deck['id']}?format=commander")).json()
    assert detail["fmt"] == "commander"
    assert detail["illegal_count"] == 0 and detail["is_legal"] is True
    assert deck["main"][0]["set_code"] == "cmm"

    card_id = deck["main"][0]["card_id"]
    resp = await client.patch(
        f"/api/v1/decks/{deck['id']}/cards/{card_id}",
        json={"scryfall_id": variant["id"], "language": "JA", "proxy": True, "special": True},
    )
    assert resp.status_code == 200
    updated = (await client.get(f"/api/v1/decks/{deck['id']}?format=commander")).json()
    r = updated["main"][0]
    assert r["set_code"] == "art" and r["language"] == "ja" and r["proxy"] and r["special"]
    # Still legal despite pointing at the non-playable printing.
    assert updated["illegal_count"] == 0


@pytest.mark.asyncio
async def test_api_collection_list_update_delete(client, session):
    await _card(session, name="Alpha", n=1, owned=2)
    await _card(session, name="Beta", n=2, owned=1)

    listing = (await client.get("/api/v1/collection?page_size=1")).json()
    assert listing["total"] == 2 and listing["total_pages"] == 2
    assert len(listing["items"]) == 1 and listing["items"][0]["name"] == "Alpha"

    row_id = listing["items"][0]["id"]
    up = await client.patch(f"/api/v1/collection/{row_id}", json={"quantity": 5, "binder": "Box A"})
    assert up.status_code == 200
    assert up.json()["quantity"] == 5 and up.json()["binder_name"] == "Box A"

    assert (await client.delete(f"/api/v1/collection/{row_id}")).status_code == 200
    assert (await client.get("/api/v1/collection")).json()["total"] == 1
    assert (await client.delete("/api/v1/collection/99999")).status_code == 404
