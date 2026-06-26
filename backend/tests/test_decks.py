"""Deck tests: decklist parsing, ownership coverage, routes, read-only guard."""

import uuid

import pytest
from sqlalchemy import func, select
from src.config import get_settings
from src.decks import create_deck, deck_coverage, parse_decklist
from src.models import Card, CollectionCard, Deck
from src.scryfall.mapping import card_to_columns


def test_parse_strips_foil_markers():
    rows = parse_decklist("1 Kaalia of the Vast (CMA) 180 *F*\n1 Anger (CMA) 76")
    assert rows[0].name == "Kaalia of the Vast"
    assert rows[1].name == "Anger"


def test_merge_lines_combines_duplicates():
    from src.decks import _merge_lines
    merged = _merge_lines(parse_decklist("5 Forest (DD1) 28\n4 Forest (DD1) 31\n3 Llanowar Elves"))
    by_name = {m.name: m.quantity for m in merged}
    assert by_name == {"Forest": 9, "Llanowar Elves": 3}


@pytest.mark.asyncio
async def test_seed_demo_decks_idempotent(session):
    from src.demo import EXAMPLE_DECKS, seed_demo_decks
    assert await seed_demo_decks() == len(EXAMPLE_DECKS)
    assert await seed_demo_decks() == 0  # decks already exist -> skipped


def test_parse_decklist_quantities_board_and_suffix():
    text = ("4 Lightning Bolt\n2x Counterspell (MH2) 267\n# comment\n\n"
            "Sideboard\n1 Naturalize\nSB: 3 Duress")
    rows = parse_decklist(text)
    assert (rows[0].quantity, rows[0].name, rows[0].board) == (4, "Lightning Bolt", "main")
    assert (rows[1].quantity, rows[1].name, rows[1].board) == (2, "Counterspell", "main")
    assert (rows[2].quantity, rows[2].name, rows[2].board) == (1, "Naturalize", "side")
    assert (rows[3].quantity, rows[3].name, rows[3].board) == (3, "Duress", "side")


async def _seed_cards(session):
    # Bolt has two printings; the owned one shares the oracle, so ownership counts either.
    oracle_bolt = str(uuid.uuid4())
    bolt_legal = {"modern": "legal", "standard": "not_legal", "commander": "legal"}
    bolt_old = {"id": str(uuid.uuid4()), "oracle_id": oracle_bolt, "name": "Lightning Bolt",
                "set": "LEA", "collector_number": "161", "rarity": "common", "cmc": 1,
                "type_line": "Instant", "colors": ["R"], "color_identity": ["R"],
                "released_at": "1993-08-05", "prices": {"usd": "5.00"}, "legalities": bolt_legal}
    bolt_new = {"id": str(uuid.uuid4()), "oracle_id": oracle_bolt, "name": "Lightning Bolt",
                "set": "MH2", "collector_number": "122", "rarity": "uncommon", "cmc": 1,
                "type_line": "Instant", "colors": ["R"], "color_identity": ["R"],
                "released_at": "2021-06-18", "prices": {"usd": "2.00"}, "legalities": bolt_legal}
    forest = {"id": str(uuid.uuid4()), "oracle_id": str(uuid.uuid4()), "name": "Forest",
              "set": "MH2", "collector_number": "490", "rarity": "common", "cmc": 0,
              "type_line": "Basic Land — Forest", "colors": [], "color_identity": ["G"],
              "prices": {"usd": "0.10"},
              "legalities": {"modern": "legal", "standard": "legal", "commander": "legal"}}
    cards = {}
    for raw in (bolt_old, bolt_new, forest):
        c = Card(**card_to_columns(raw))
        session.add(c)
        cards[raw["name"] + raw["set"]] = c
    await session.flush()
    # Own 1 Lightning Bolt (the old printing) and nothing else.
    session.add(CollectionCard(scryfall_id=cards["Lightning BoltLEA"].scryfall_id, quantity=1))
    await session.commit()
    return cards


@pytest.mark.asyncio
async def test_create_deck_resolves_and_prefers_owned_printing(session):
    cards = await _seed_cards(session)
    deck = await create_deck(session, "Burn", "4 Lightning Bolt\n20 Forest\n1 MysteryCard")
    by_name = {c.name: c for c in deck.cards}
    # Bolt resolves to the OWNED (LEA) printing, not the newer one.
    assert str(by_name["Lightning Bolt"].scryfall_id) == str(cards["Lightning BoltLEA"].scryfall_id)
    assert by_name["Forest"].oracle_id is not None
    assert by_name["MysteryCard"].oracle_id is None  # unrecognized


@pytest.mark.asyncio
async def test_coverage_counts_owned_and_missing(session):
    await _seed_cards(session)
    deck = await create_deck(session, "Burn", "4 Lightning Bolt\n20 Forest\n1 MysteryCard")
    cov = await deck_coverage(session, deck)
    assert cov.total_needed == 25
    # Own 1 Bolt -> missing 3 Bolt + 20 Forest + 1 unmatched = 24.
    assert cov.missing_count == 24
    assert cov.owned_count == 1
    assert cov.unmatched == 1
    assert cov.unique_missing == 3  # Bolt, Forest, the unmatched line
    # Missing cost = 3 Bolt * $5 (owned LEA printing's price) + 20 Forest * $0.10.
    assert round(cov.missing_cost, 2) == 17.00


@pytest.mark.asyncio
async def test_legality_check(session):
    await _seed_cards(session)
    deck = await create_deck(session, "Burn", "4 Lightning Bolt\n20 Forest")
    legal = await deck_coverage(session, deck, fmt="modern")
    assert legal.fmt == "modern" and legal.illegal_count == 0 and legal.is_legal
    illegal = await deck_coverage(session, deck, fmt="standard")
    assert illegal.illegal_count == 1 and not illegal.is_legal  # Bolt isn't standard-legal
    # An unknown / blank format clears the check.
    assert (await deck_coverage(session, deck, fmt="bogus")).fmt is None


@pytest.mark.asyncio
async def test_deck_routes_and_delete(client, session):
    await _seed_cards(session)
    resp = await client.post("/decks", data={"name": "D", "decklist": "4 Lightning Bolt"},
                             follow_redirects=True)
    assert resp.status_code == 200
    assert "% owned" in resp.text
    deck_id = await session.scalar(select(Deck.id))
    assert (await client.get(f"/decks/{deck_id}")).status_code == 200
    assert (await client.get("/decks/99999")).status_code == 404

    await client.post(f"/decks/{deck_id}/delete", follow_redirects=True)
    assert await session.scalar(select(func.count()).select_from(Deck)) == 0


@pytest.mark.asyncio
async def test_read_only_blocks_deck_mutations(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "read_only", True)
    create = await client.post("/decks", data={"name": "x", "decklist": "1 Forest"})
    assert create.status_code == 403
    assert (await client.get("/decks/new")).status_code == 403
    assert (await client.post("/decks/1/delete")).status_code == 403
