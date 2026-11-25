"""Coverage tests for src/routes/card.py: detail page, image redirect, similar, tags, rulings."""

import uuid

import pytest
from src.card import _cache
from src.models import Card, CardEmbedding, CollectionCard
from src.routes import card as card_route
from src.scryfall.mapping import card_to_columns


async def _card(session, *, oracle=None, name="Lightning Bolt", n=1, **extra):
    raw = {"id": str(uuid.uuid4()), "oracle_id": str(oracle) if oracle else str(uuid.uuid4()),
           "name": name, "set": "mh2", "collector_number": str(n), "rarity": "uncommon",
           "cmc": 1, "type_line": "Instant", "colors": ["R"], "color_identity": ["R"],
           "released_at": "2021-06-18", "oracle_text": "Deal 3 damage.",
           "prices": {"usd": "2.50", "usd_foil": "5.00", "eur": "2.00", "tix": "0.10"},
           "legalities": {"modern": "legal"}, "scryfall_uri": "https://sf.test/x",
           "artist": "Rush"}
    raw.update(extra)
    c = Card(**card_to_columns(raw))
    session.add(c)
    await session.commit()
    return c


# --- detail page --------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_card_detail_full(client, session):
    oracle = uuid.uuid4()
    c = await _card(session, oracle=oracle, n=1)
    await _card(session, oracle=oracle, n=2)  # sibling printing
    session.add(CollectionCard(scryfall_id=c.scryfall_id, quantity=2, finish="foil"))
    await session.commit()
    resp = await client.get(f"/card/{c.scryfall_id}")
    assert resp.status_code == 200
    assert "Lightning Bolt" in resp.text
    assert "Other printings" in resp.text


@pytest.mark.asyncio
async def test_card_detail_shows_similar_when_embedding_exists(client, session):
    oracle = uuid.uuid4()
    c = await _card(session, oracle=oracle)
    session.add(CardEmbedding(oracle_id=oracle, model="test", dim=4, vector=[0.1] * 4))
    await session.commit()
    resp = await client.get(f"/card/{c.scryfall_id}")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_card_detail_eur_currency_price_rows(client, session):
    c = await _card(session)
    resp = await client.get(f"/card/{c.scryfall_id}", cookies={"scryme_currency": "eur"})
    assert resp.status_code == 200


def _req(**cookies):
    """A bare request carrying only cookies (no prefs middleware), as the helpers accept."""
    from starlette.requests import Request
    header = "; ".join(f"{k}={v}" for k, v in cookies.items()).encode()
    return Request({"type": "http", "headers": [(b"cookie", header)] if cookies else []})


@pytest.mark.asyncio
async def test_price_rows_name_every_source_and_flag_the_preferred_one(session, monkeypatch):
    """#410: the charted TCGplayer price is always listed, beside the preferred marketplace's."""
    c = await _card(session)
    c.market_prices = {"manapool": {"usd": "1.84", "usd_foil": "25.00"}}
    before = {"manapool": dict(c.market_prices["manapool"])}

    rows = card_route._price_rows(_req(scryme_price_source="manapool"), c)
    assert [(r["label"], r["preferred"]) for r in rows] == [("ManaPool", True),
                                                             ("TCGplayer", False)]
    assert rows[0]["prices"] == [{"label": "normal", "amount": 1.84, "currency": "usd"},
                                 {"label": "✦ foil", "amount": 25.0, "currency": "usd"}]
    assert rows[1]["prices"][0] == {"label": "normal", "amount": 2.5, "currency": "usd"}
    assert c.market_prices == before          # building the rows must not rewrite the card
    assert "ManaPool" in card_route._chart_source_note(_req(), rows)

    # With TCGplayer preferred the chips and the chart agree, so there is nothing to explain.
    rows = card_route._price_rows(_req(), c)
    assert rows[0]["code"] == "tcgplayer" and rows[0]["preferred"]
    assert card_route._chart_source_note(_req(), rows) is None

    # A converted currency converts every source; without a rate the amount stays, honestly, USD.
    monkeypatch.setitem(card_route.fx.FX_RATES, "cad", 1.4)
    cad = card_route._price_rows(_req(scryme_currency="cad"), c)
    assert cad[0]["prices"][0] == {"label": "normal", "amount": pytest.approx(3.5),
                                   "currency": "cad"}
    monkeypatch.delitem(card_route.fx.FX_RATES, "cad")
    unconverted = card_route._price_rows(_req(scryme_currency="cad"), c)
    assert unconverted[0]["prices"][0] == {"label": "normal", "amount": 2.5, "currency": "usd"}


@pytest.mark.asyncio
async def test_price_rows_in_euro_lead_with_cardmarket(session):
    c = await _card(session)
    rows = card_route._price_rows(_req(scryme_currency="eur"), c)
    assert rows[0]["label"] == "Cardmarket" and rows[0]["preferred"]
    assert rows[0]["prices"] == [{"label": "normal", "amount": 2.0, "currency": "eur"}]
    assert rows[1]["label"] == "TCGplayer" and rows[1]["prices"][0]["currency"] == "usd"


@pytest.mark.asyncio
async def test_card_detail_labels_price_sources_and_the_chart(client, session):
    c = await _card(session)
    c.market_prices = {"manapool": {"usd": "1.84"}}
    await session.commit()
    page = (await client.get(f"/card/{c.scryfall_id}",
                             cookies={"scryme_price_source": "manapool"})).text
    assert "ManaPool" in page and "$1.84" in page and "$2.50" in page
    assert "TCGplayer market price for the non-foil printing" in page
    assert "history is only recorded from TCGplayer" in page
    # Tix are event tickets, not dollars.
    assert "<b>0.10</b> tix" in page and "$0.10" not in page

    plain = (await client.get(f"/card/{c.scryfall_id}")).text
    assert "history is only recorded from" not in plain


@pytest.mark.asyncio
async def test_card_detail_404(client):
    assert (await client.get("/card/not-a-uuid")).status_code == 404
    assert (await client.get(f"/card/{uuid.uuid4()}")).status_code == 404


# --- image redirect -----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_card_image_redirects(client, session):
    c = await _card(session, image_uris={"normal": "https://img.test/bolt.jpg"})
    resp = await client.get(f"/card/{c.scryfall_id}/image", follow_redirects=False)
    assert resp.status_code == 307
    assert resp.headers["location"] == "https://img.test/bolt.jpg"


@pytest.mark.asyncio
async def test_card_image_no_image_404(client, session):
    # A card whose raw carries no image uris at all -> _image() returns "" -> 404.
    raw = {"id": str(uuid.uuid4()), "oracle_id": str(uuid.uuid4()), "name": "No Art",
           "set": "tst", "collector_number": "1", "rarity": "common"}
    c = Card(**card_to_columns(raw))
    c.raw = {k: v for k, v in c.raw.items() if k != "image_uris"}
    c.raw.pop("card_faces", None)
    session.add(c)
    await session.commit()
    resp = await client.get(f"/card/{c.scryfall_id}/image", follow_redirects=False)
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_card_image_uses_cache(client, session, monkeypatch):
    c = await _card(session, image_uris={"normal": "https://img.test/x.jpg"})
    monkeypatch.setattr(card_route._cache, "is_cached", lambda sid: True)
    monkeypatch.setattr(_cache, "url_path", lambda sid: "/images/x.jpg")
    resp = await client.get(f"/card/{c.scryfall_id}/image", follow_redirects=False)
    assert resp.status_code == 307
    assert resp.headers["location"].endswith("/x.jpg")


# --- similar grid -------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_card_similar_empty(client, session):
    # No embeddings -> empty grid, still renders.
    c = await _card(session)
    resp = await client.get(f"/card/{c.scryfall_id}/similar")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_card_similar_with_matches(client, session, monkeypatch):
    oracle = uuid.uuid4()
    c = await _card(session, oracle=oracle)
    other_oracle = uuid.uuid4()
    await _card(session, oracle=other_oracle, name="Shock", n=5)

    async def fake_similar(sess, oid, limit=8, scope="owned"):
        return [(other_oracle, 0.9)]

    monkeypatch.setattr(card_route, "similar_to_oracle", fake_similar)
    resp = await client.get(f"/card/{c.scryfall_id}/similar")
    assert resp.status_code == 200
    assert "Shock" in resp.text


# --- rulings ------------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_rulings_from_cache(client, session):
    c = await _card(session)
    sid = str(c.scryfall_id)
    card_route._rulings_cache[sid] = [{"published_at": "2020-01-01", "comment": "Timing note."}]
    try:
        resp = await client.get(f"/card/{sid}/rulings")
        assert resp.status_code == 200 and "Timing note." in resp.text
    finally:
        card_route._rulings_cache.pop(sid, None)


@pytest.mark.asyncio
async def test_rulings_fetch_from_scryfall(client, session, monkeypatch):
    c = await _card(session, rulings_uri="https://sf.test/rulings")
    sid = str(c.scryfall_id)
    card_route._rulings_cache.pop(sid, None)

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get_json(self, uri):
            return {"data": [{"published_at": "2021-01-01", "comment": "Fetched ruling."}]}

    monkeypatch.setattr(card_route, "ScryfallClient", FakeClient)
    try:
        resp = await client.get(f"/card/{sid}/rulings")
        assert resp.status_code == 200 and "Fetched ruling." in resp.text
        # Second call is served from the cache (no client needed).
        assert card_route._rulings_cache[sid]
    finally:
        card_route._rulings_cache.pop(sid, None)


@pytest.mark.asyncio
async def test_rulings_missing_uri_degrades(client, session):
    c = await _card(session)
    card_route._rulings_cache.pop(str(c.scryfall_id), None)
    resp = await client.get(f"/card/{c.scryfall_id}/rulings")
    assert resp.status_code == 200
