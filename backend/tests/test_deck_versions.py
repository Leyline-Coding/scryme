"""Deck versioning + diff tests (#100)."""

import uuid

import pytest
from sqlalchemy import func, select
from src.config import get_settings
from src.deck_versions import diff_cards, list_versions, save_version, snapshot_cards
from src.decks import create_deck
from src.models import Card, Deck, DeckVersion
from src.scryfall.mapping import card_to_columns


def _card(name, board="main", qty=1):
    return {"name": name, "quantity": qty, "board": board, "oracle_id": None, "scryfall_id": None}


# --- pure diff ---------------------------------------------------------------

def test_diff_added_removed_changed_unchanged():
    a = [_card("Sol Ring"), _card("Llanowar Elves"), _card("Forest", qty=10)]
    b = [_card("Sol Ring"), _card("Cultivate"), _card("Forest", qty=8)]
    main = diff_cards(a, b).boards[0]
    assert [c.name for c in main.added] == ["Cultivate"]        # in B only
    assert [c.name for c in main.removed] == ["Llanowar Elves"]  # in A only
    assert len(main.changed) == 1 and main.changed[0].name == "Forest"
    assert (main.changed[0].from_qty, main.changed[0].to_qty) == (10, 8)
    assert main.unchanged == 1  # Sol Ring


def test_diff_separates_boards():
    a = [_card("Bolt", "main"), _card("Duress", "side")]
    b = [_card("Bolt", "main", qty=2), _card("Naturalize", "side")]
    diff = diff_cards(a, b)
    main, side = diff.boards
    assert main.board == "main" and [c.name for c in main.changed] == ["Bolt"]
    assert side.board == "side"
    assert [c.name for c in side.added] == ["Naturalize"]
    assert [c.name for c in side.removed] == ["Duress"]


def test_diff_merges_case_and_duplicate_lines():
    # Same card across two lines / cases sums before diffing.
    a = [_card("sol ring"), _card("Sol Ring")]
    b = [_card("Sol Ring", qty=2)]
    assert diff_cards(a, b).boards[0].unchanged == 1  # 2 vs 2 -> unchanged
    assert not diff_cards(a, b).has_changes


def test_identical_lists_have_no_changes():
    a = [_card("Sol Ring"), _card("Island", "main", qty=12)]
    assert diff_cards(a, list(a)).has_changes is False


# --- snapshot + persistence --------------------------------------------------

async def _seed(session):
    for name in ("Sol Ring", "Llanowar Elves", "Cultivate"):
        session.add(Card(**card_to_columns(
            {"id": str(uuid.uuid4()), "oracle_id": str(uuid.uuid4()), "name": name,
             "set": "TST", "collector_number": "1", "type_line": "Artifact",
             "legalities": {"commander": "legal"}, "prices": {"usd": "1"}})))
    await session.commit()


@pytest.mark.asyncio
async def test_snapshot_cards_serializes_deck(session):
    await _seed(session)
    deck = await create_deck(session, "D", "1 Sol Ring\nSideboard\n1 Cultivate")
    snap = snapshot_cards(deck)
    assert {(c["name"], c["board"]) for c in snap} == {("Sol Ring", "main"), ("Cultivate", "side")}
    assert all(isinstance(c["oracle_id"], str) for c in snap)  # UUIDs stringified


@pytest.mark.asyncio
async def test_save_version_auto_labels_and_lists_newest_first(session):
    await _seed(session)
    deck = await create_deck(session, "D", "1 Sol Ring")
    v1 = await save_version(session, deck, "")
    v2 = await save_version(session, deck, "  ")   # blank -> auto
    v3 = await save_version(session, deck, "my snapshot")
    assert (v1.label, v2.label, v3.label) == ("v1", "v2", "my snapshot")
    versions = await list_versions(session, deck.id)
    assert [v.id for v in versions][0] == v3.id  # newest first


# --- routes ------------------------------------------------------------------

@pytest.mark.asyncio
async def test_save_version_route_shows_on_deck_page(client, session):
    await _seed(session)
    deck = await create_deck(session, "D", "1 Sol Ring")
    resp = await client.post(f"/decks/{deck.id}/versions", data={"label": "opening"},
                             follow_redirects=True)
    assert resp.status_code == 200 and "opening" in resp.text
    assert await session.scalar(select(func.count()).select_from(DeckVersion)) == 1
    assert (await client.post("/decks/99999/versions", data={})).status_code == 404


@pytest.mark.asyncio
async def test_diff_route_current_vs_version(client, session):
    await _seed(session)
    deck = await create_deck(session, "D", "1 Sol Ring\n1 Llanowar Elves")
    version = await save_version(session, deck, "before")
    # Edit the deck: drop Elves, add Cultivate.
    deck2 = await session.get(Deck, deck.id)
    deck2.cards[1].name = "Cultivate"
    await session.commit()

    resp = await client.get(f"/decks/{deck.id}/diff?a={version.id}&b=current")
    assert resp.status_code == 200
    assert "Cultivate" in resp.text and "Llanowar Elves" in resp.text  # added + removed shown

    # Default (no a) uses the newest version -> current.
    assert (await client.get(f"/decks/{deck.id}/diff")).status_code == 200
    # Bad version id 404s; a numeric-but-missing version 404s; missing deck 404s.
    assert (await client.get(f"/decks/{deck.id}/diff?a=abc&b=current")).status_code == 404
    assert (await client.get(f"/decks/{deck.id}/diff?a=999999&b=current")).status_code == 404
    assert (await client.get("/decks/99999/diff")).status_code == 404

    # A version id belonging to another deck is not accepted for this deck.
    other = await create_deck(session, "Other", "1 Sol Ring")
    other_v = await save_version(session, other, "z")
    assert (await client.get(
        f"/decks/{deck.id}/diff?a={other_v.id}&b=current")).status_code == 404


@pytest.mark.asyncio
async def test_diff_no_versions_defaults_current_vs_current(client, session):
    await _seed(session)
    deck = await create_deck(session, "D", "1 Sol Ring")
    resp = await client.get(f"/decks/{deck.id}/diff")  # no versions -> current vs current
    assert resp.status_code == 200 and "No differences" in resp.text


@pytest.mark.asyncio
async def test_delete_version_and_cascade_on_deck_delete(client, session):
    await _seed(session)
    deck = await create_deck(session, "D", "1 Sol Ring")
    v = await save_version(session, deck, "x")
    await client.post(f"/decks/{deck.id}/versions/{v.id}/delete", follow_redirects=True)
    assert await session.scalar(select(func.count()).select_from(DeckVersion)) == 0
    # Deleting a non-matching version id is a no-op (still 303).
    v2 = await save_version(session, deck, "y")
    assert (await client.post(f"/decks/{deck.id}/versions/999999/delete")).status_code in (303, 200)
    # Deleting the deck cascades to its versions.
    assert v2 is not None
    await client.post(f"/decks/{deck.id}/delete")
    assert await session.scalar(select(func.count()).select_from(DeckVersion)) == 0


@pytest.mark.asyncio
async def test_version_routes_read_only(client, session, monkeypatch):
    await _seed(session)
    deck = await create_deck(session, "D", "1 Sol Ring")
    monkeypatch.setattr(get_settings(), "read_only", True)
    assert (await client.post(f"/decks/{deck.id}/versions", data={})).status_code == 403
    assert (await client.post(f"/decks/{deck.id}/versions/1/delete")).status_code == 403
