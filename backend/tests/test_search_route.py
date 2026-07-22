"""Search route tests: full page vs HTMX partial, scope, and error rendering."""

import uuid
from types import SimpleNamespace

import pytest
from src.models import Card, CollectionCard
from src.routes.search import _treatment
from src.scryfall.mapping import card_to_columns


def test_resolve_page_size():
    from src.routes.search import _resolve_page_size

    def req(val):
        return SimpleNamespace(cookies={} if val is None else {"scryme_page_size": val})
    assert _resolve_page_size(req("120")) == 120
    assert _resolve_page_size(req("999")) == 60   # not an offered size -> default
    assert _resolve_page_size(req("abc")) == 60
    assert _resolve_page_size(req(None)) == 60


@pytest.mark.asyncio
async def test_page_size_controls_and_infinite_append(client, session):
    await _seed_owned(session)
    # Page-size selector + infinite toggle render on the full page.
    page = await client.get("/search?scope=all&q=Lightning")
    assert 'id="page-size"' in page.text and "∞ scroll" in page.text
    # The infinite-scroll "load more" request returns a minimal fragment (cards, no page header).
    frag = await client.get("/search?scope=all&q=Lightning&page=1&append=1",
                            headers={"HX-Request": "true"})
    assert frag.status_code == 200
    assert "in your collection" not in frag.text and 'id="page-size"' not in frag.text


def test_treatment_foil_only_and_etched():
    def card(finishes):
        return SimpleNamespace(raw={"finishes": finishes})
    assert _treatment(card(["etched"])) == "etched"
    assert _treatment(card(["foil", "etched"])) == "etched"     # etched wins
    assert _treatment(card(["foil"])) == "foil"                 # foil-only
    assert _treatment(card(["nonfoil", "foil"])) is None        # ordinary card
    assert _treatment(card([])) is None
    assert _treatment(SimpleNamespace(raw={})) is None


async def _seed_owned(session):
    raw = {"id": str(uuid.uuid4()), "name": "Lightning Bolt", "set": "MH2",
           "collector_number": "122", "rarity": "uncommon", "cmc": 1, "type_line": "Instant",
           "colors": ["R"], "color_identity": ["R"], "scryfall_uri": "https://scryfall.test/bolt",
           "oracle_text": "deals 3 damage", "prices": {"usd": "2.50"}}
    card = Card(**card_to_columns(raw))
    session.add(card)
    await session.flush()
    session.add(CollectionCard(scryfall_id=card.scryfall_id, quantity=2))
    await session.commit()
    return card


@pytest.mark.asyncio
async def test_grid_flip_button_for_double_faced(client, session):
    dfc = {"id": str(uuid.uuid4()), "name": "Delver of Secrets // Insectile Aberration",
           "set": "isd", "collector_number": "51", "rarity": "common", "cmc": 1,
           "layout": "transform", "type_line": "Creature — Human Wizard",
           "colors": ["U"], "color_identity": ["U"],
           "card_faces": [
               {"name": "Delver of Secrets", "image_uris": {"normal": "https://img.test/f.jpg"}},
               {"name": "Insectile Aberration", "image_uris": {"normal": "https://img.test/b.jpg"}},
           ]}
    single = {"id": str(uuid.uuid4()), "name": "Grizzly Bears", "set": "isd",
              "collector_number": "1", "rarity": "common", "cmc": 2, "type_line": "Creature — Bear",
              "colors": ["G"], "color_identity": ["G"], "image_uris": {"normal": "https://img.test/g.jpg"}}
    for raw in (dfc, single):
        c = Card(**card_to_columns(raw))
        session.add(c)
        await session.flush()
        session.add(CollectionCard(scryfall_id=c.scryfall_id, quantity=1))
    await session.commit()

    resp = await client.get("/search", params={"q": ""})
    assert resp.status_code == 200
    body = resp.text
    assert 'data-back="https://img.test/b.jpg"' in body  # back face wired up
    # Exactly one flip button — the DFC gets it, the single-faced Grizzly Bears doesn't.
    assert body.count('aria-label="Flip card"') == 1


@pytest.mark.asyncio
async def test_full_page_render(client, session):
    await _seed_owned(session)
    resp = await client.get("/search", params={"q": "bolt"})
    assert resp.status_code == 200
    assert "<html" in resp.text  # full document
    assert "Lightning Bolt" in resp.text
    assert "×2" in resp.text  # owned quantity badge


@pytest.mark.asyncio
async def test_htmx_returns_partial(client, session):
    await _seed_owned(session)
    resp = await client.get("/search", params={"q": "bolt"}, headers={"HX-Request": "true"})
    assert resp.status_code == 200
    assert "<html" not in resp.text  # partial only
    assert "Lightning Bolt" in resp.text


@pytest.mark.asyncio
async def test_invalid_query_shows_error(client):
    resp = await client.get("/search", params={"q": "bogus:value"}, headers={"HX-Request": "true"})
    assert resp.status_code == 200
    assert "Unknown filter" in resp.text


@pytest.mark.asyncio
async def test_scope_all_searches_unowned(client, session):
    # Seed a card NOT in the collection; collection scope hides it, all scope finds it.
    raw = {"id": str(uuid.uuid4()), "name": "Black Lotus", "set": "LEA",
           "collector_number": "232", "rarity": "rare", "cmc": 0, "type_line": "Artifact",
           "colors": [], "color_identity": []}
    session.add(Card(**card_to_columns(raw)))
    await session.commit()

    owned = await client.get("/search", params={"q": "lotus", "scope": "collection"},
                             headers={"HX-Request": "true"})
    assert "Black Lotus" not in owned.text

    all_cards = await client.get("/search", params={"q": "lotus", "scope": "all"},
                                 headers={"HX-Request": "true"})
    assert "Black Lotus" in all_cards.text
