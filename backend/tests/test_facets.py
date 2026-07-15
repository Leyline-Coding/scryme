"""Faceted browse: facet counts, the token-toggle, and the search-page integration."""

import uuid

import pytest
from src.facets import _toggle, compute_facets
from src.models import Card, CollectionCard
from src.scryfall.mapping import card_to_columns
from src.search import SearchScope
from src.search.engine import run_search


def test_toggle_adds_and_removes():
    assert _toggle("t:creature", "c:r") == (False, "t:creature c:r")
    assert _toggle("t:creature c:r", "c:r") == (True, "t:creature")
    # Case-insensitive match on removal.
    assert _toggle("C:R", "c:r") == (True, "")
    assert _toggle("", "r:rare") == (False, "r:rare")


async def _seed(session):
    cards = [
        # name, colors, rarity, type_line, set, set_name, released, legal_commander, foil
        ("Bear", ["G"], "common", "Creature — Bear", "tst", "Test Set", "2021-01-01", True, True),
        ("Bolt", ["R"], "common", "Instant", "tst", "Test Set", "2021-06-01", True, False),
        ("Hybrid", ["R", "G"], "rare", "Creature", "oth", "Other Set", "2023-02-01", True, True),
        ("Rock", [], "uncommon", "Artifact", "oth", "Other Set", "2023-09-01", False, False),
    ]
    # Owned finishes per printing (is:foil / is:etched match the finish you own, not mere
    # foil-capability): Bear owned in foil, Hybrid owned in both foil and etched.
    owned_finishes = {"Bear": ["foil"], "Bolt": ["normal"],
                      "Hybrid": ["foil", "etched"], "Rock": ["normal"]}
    for i, (name, colors, rarity, tl, sc, sn, rel, legal, foil) in enumerate(cards):
        finishes = ["nonfoil"] + (["foil"] if foil else [])
        if name == "Hybrid":
            finishes.append("etched")   # Hybrid is available in etched foil
        raw = {"id": str(uuid.uuid4()), "oracle_id": str(uuid.uuid4()), "name": name,
               "set": sc, "set_name": sn, "collector_number": str(i), "rarity": rarity,
               "type_line": tl, "colors": colors, "color_identity": colors,
               "released_at": rel, "foil": foil, "finishes": finishes,
               "legalities": {"commander": "legal" if legal else "not_legal"},
               "prices": {"usd": "1.00"}}
        c = Card(**card_to_columns(raw))
        session.add(c)
        await session.flush()
        for fin in owned_finishes[name]:
            session.add(CollectionCard(scryfall_id=c.scryfall_id, quantity=1, finish=fin))
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
async def test_year_legality_foil_facets(session):
    await _seed(session)
    groups = {g.key: g for g in await compute_facets(session, "", SearchScope.COLLECTION)}

    years = {v.label: (v.token, v.count) for v in groups["year"].values}
    assert years["2023"] == ("year:2023", 2) and years["2021"] == ("year:2021", 2)
    assert [v.label for v in groups["year"].values] == ["2023", "2021"]  # recent first

    legality = {v.label: (v.token, v.count) for v in groups["legality"].values}
    assert legality["Commander"] == ("f:commander", 3)  # 3 of 4 are commander-legal

    finish = {v.label: (v.token, v.count) for v in groups["foil"].values}
    assert finish["Foil"] == ("is:foil", 2)      # owned in foil: Bear + Hybrid
    assert finish["Etched"] == ("is:etched", 1)  # owned in etched: Hybrid

    # is:foil / is:etched match the finish you OWN, not mere foil-capability of the printing.
    foil_res = await run_search(session, "is:foil", scope=SearchScope.COLLECTION)
    assert sorted(c.name for c in foil_res.cards) == ["Bear", "Hybrid"]
    etched_res = await run_search(session, "is:etched", scope=SearchScope.COLLECTION)
    assert [c.name for c in etched_res.cards] == ["Hybrid"]


@pytest.mark.asyncio
async def test_view_toggle_grid_vs_list(client, session):
    await _seed(session)
    grid = await client.get("/search?q=")
    assert "card-grid" in grid.text and "<table" not in grid.text
    lst = await client.get("/search?q=", headers={"Cookie": "scryme_view=list"})
    assert "<table" in lst.text and "<thead" in lst.text


@pytest.mark.asyncio
async def test_search_page_renders_facets(client, session):
    await _seed(session)
    resp = await client.get("/search?q=")
    assert resp.status_code == 200
    assert "Colors" in resp.text and "Rarity" in resp.text
    # Facet buttons carry the toggled query in data-q (read by a delegated click handler) — the
    # value must be a normal HTML attribute, not broken inline-JS double quotes.
    assert 'class="facet-btn' in resp.text
    assert 'data-q="c:r"' in resp.text
    assert 'onclick="applyFacet(' not in resp.text
