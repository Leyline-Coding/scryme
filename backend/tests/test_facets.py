"""Faceted browse: facet counts, the token-toggle, and the search-page integration."""

import uuid

import pytest
from src.facets import _toggle, compute_facets
from src.models import Card, CollectionCard
from src.scryfall.mapping import card_to_columns
from src.search import SearchScope


def test_toggle_adds_and_removes():
    assert _toggle("t:creature", "c:r") == (False, "t:creature c:r")
    assert _toggle("t:creature c:r", "c:r") == (True, "t:creature")
    # Case-insensitive match on removal.
    assert _toggle("C:R", "c:r") == (True, "")
    assert _toggle("", "r:rare") == (False, "r:rare")


async def _seed(session):
    cards = [
        # name, colors, rarity, type_line, set, set_name
        ("Bear", ["G"], "common", "Creature — Bear", "tst", "Test Set"),
        ("Bolt", ["R"], "common", "Instant", "tst", "Test Set"),
        ("Hybrid", ["R", "G"], "rare", "Creature — Beast", "oth", "Other Set"),
        ("Rock", [], "uncommon", "Artifact", "oth", "Other Set"),
    ]
    for i, (name, colors, rarity, tl, sc, sn) in enumerate(cards):
        raw = {"id": str(uuid.uuid4()), "oracle_id": str(uuid.uuid4()), "name": name,
               "set": sc, "set_name": sn, "collector_number": str(i), "rarity": rarity,
               "type_line": tl, "colors": colors, "color_identity": colors,
               "prices": {"usd": "1.00"}}
        c = Card(**card_to_columns(raw))
        session.add(c)
        await session.flush()
        session.add(CollectionCard(scryfall_id=c.scryfall_id, quantity=1))
    await session.commit()


@pytest.mark.asyncio
async def test_compute_facets_counts(session):
    await _seed(session)
    groups = {g.key: g for g in await compute_facets(session, "", SearchScope.COLLECTION)}

    colors = {v.label: v.count for v in groups["colors"].values}
    assert colors == {"Red": 2, "Green": 2, "Colorless": 1}  # Hybrid counts in both R and G

    rarity = {v.label: v.count for v in groups["rarity"].values}
    assert rarity == {"Common": 2, "Uncommon": 1, "Rare": 1}
    # Rarity facet keeps the common<uncommon<rare order.
    assert [v.label for v in groups["rarity"].values] == ["Common", "Uncommon", "Rare"]

    types = {v.label: v.count for v in groups["type"].values}
    assert types == {"Creature": 2, "Instant": 1, "Artifact": 1}

    sets = {v.label: v.count for v in groups["set"].values}
    assert sets == {"Test Set": 2, "Other Set": 2}


@pytest.mark.asyncio
async def test_facet_value_tokens_and_toggle(session):
    await _seed(session)
    groups = {g.key: g for g in await compute_facets(session, "t:creature", SearchScope.COLLECTION)}
    red = next(v for v in groups["colors"].values if v.label == "Red")
    assert red.token == "c:r"
    assert not red.active
    assert red.new_query == "t:creature c:r"


@pytest.mark.asyncio
async def test_search_page_renders_facets(client, session):
    await _seed(session)
    resp = await client.get("/search?q=")
    assert resp.status_code == 200
    assert "applyFacet(" in resp.text   # facet buttons wired up
    assert "Colors" in resp.text and "Rarity" in resp.text
