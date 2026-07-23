"""Deck tests: decklist parsing, ownership coverage, routes, read-only guard."""

import uuid

import pytest
from sqlalchemy import func, select
from src.config import get_settings
from src.decks import create_deck, deck_coverage, parse_decklist
from src.models import Card, CollectionCard, Deck
from src.scryfall.mapping import card_to_columns


def test_parse_captures_printing_and_finish():
    rows = parse_decklist("1 Kaalia of the Vast (CMA) 180 *F*\n1 Anger (CMA) 76\n2 Sol Ring")
    assert rows[0].name == "Kaalia of the Vast"
    assert (rows[0].set_code, rows[0].collector_number, rows[0].finish) == ("CMA", "180", "foil")
    assert rows[1].name == "Anger"
    assert (rows[1].set_code, rows[1].collector_number, rows[1].finish) == ("CMA", "76", "normal")
    # A bare line still parses, with no printing hint.
    assert (rows[2].name, rows[2].set_code, rows[2].finish) == ("Sol Ring", "", "normal")


def test_parse_tolerates_a_stray_asterisk():
    """A lone trailing "*" isn't a finish marker, so it stays part of the name."""
    row = parse_decklist("1 Weird Name*")[0]
    assert row.name == "Weird Name*" and row.finish == "normal"


def test_parse_etched_marker():
    row = parse_decklist("1 Command Tower (CMR) 350 *E*")[0]
    assert row.finish == "etched" and row.collector_number == "350"


def test_merge_lines_keeps_distinct_printings():
    from src.decks import _merge_lines
    # Different collector numbers are different printings now — they must NOT be merged.
    merged = _merge_lines(parse_decklist("5 Forest (DD1) 28\n4 Forest (DD1) 31\n3 Llanowar Elves"))
    assert [(m.name, m.quantity, m.collector_number) for m in merged] == [
        ("Forest", 5, "28"), ("Forest", 4, "31"), ("Llanowar Elves", 3, ""),
    ]
    # Truly identical lines still combine.
    same = _merge_lines(parse_decklist("2 Forest (DD1) 28\n3 Forest (DD1) 28"))
    assert [(m.name, m.quantity) for m in same] == [("Forest", 5)]
    # Same printing in different finishes stays separate.
    finishes = _merge_lines(parse_decklist("1 Sol Ring (CMR) 1\n1 Sol Ring (CMR) 1 *F*"))
    assert [(m.finish, m.quantity) for m in finishes] == [("normal", 1), ("foil", 1)]


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
async def test_create_deck_honours_explicit_printing(session):
    """An explicit (SET) NUM wins over the 'prefer a printing you own' default."""
    cards = await _seed_cards(session)   # the LEA Bolt is owned, the MH2 one isn't
    deck = await create_deck(session, "Burn", "1 Lightning Bolt (MH2) 122")
    line = deck.cards[0]
    assert str(line.scryfall_id) == str(cards["Lightning BoltMH2"].scryfall_id)
    # Sanity: without the hint it still falls back to the owned printing.
    bare = await create_deck(session, "Burn2", "1 Lightning Bolt")
    assert str(bare.cards[0].scryfall_id) == str(cards["Lightning BoltLEA"].scryfall_id)


@pytest.mark.asyncio
async def test_create_deck_unknown_printing_falls_back_to_name(session):
    cards = await _seed_cards(session)
    # A printing we don't have falls back to the by-name pick rather than going unmatched.
    deck = await create_deck(session, "Burn", "1 Lightning Bolt (ZZZ) 999")
    assert str(deck.cards[0].scryfall_id) == str(cards["Lightning BoltLEA"].scryfall_id)


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


async def _seed_signet(session):
    """A card with a tournament-legal printing and a NEWER non-playable variant (art-series style,
    ``not_legal`` in every format) sharing one oracle id."""
    oracle = str(uuid.uuid4())
    legal = {"commander": "legal", "modern": "not_legal", "vintage": "legal"}
    dead = {"commander": "not_legal", "modern": "not_legal", "vintage": "not_legal"}
    playable = {"id": str(uuid.uuid4()), "oracle_id": oracle, "name": "Boros Signet",
                "set": "CMM", "collector_number": "942", "rarity": "common", "cmc": 2,
                "type_line": "Artifact", "colors": [], "color_identity": ["R", "W"],
                "released_at": "2022-01-01", "prices": {"usd": "1.00"}, "legalities": legal}
    variant = {"id": str(uuid.uuid4()), "oracle_id": oracle, "name": "Boros Signet",
               "set": "AART", "collector_number": "5", "rarity": "common", "cmc": 2,
               "type_line": "Artifact", "colors": [], "color_identity": ["R", "W"], "layout":
               "art_series", "released_at": "2099-01-01", "prices": {"usd": "9.00"},
               "legalities": dead}
    cards = {}
    for raw in (playable, variant):
        c = Card(**card_to_columns(raw))
        session.add(c)
        cards[raw["set"]] = c
    await session.commit()
    return oracle, cards


@pytest.mark.asyncio
async def test_resolution_prefers_playable_over_newer_variant(session):
    _oracle, cards = await _seed_signet(session)
    deck = await create_deck(session, "C", "1 Boros Signet")
    # The newer art-series variant is skipped for the tournament-legal printing.
    assert str(deck.cards[0].scryfall_id) == str(cards["CMM"].scryfall_id)


@pytest.mark.asyncio
async def test_legality_by_oracle_ignores_nonplayable_printing(session):
    _oracle, cards = await _seed_signet(session)
    deck = await create_deck(session, "C", "1 Boros Signet")
    # Force the line onto the non-playable printing (as pre-fix data / a manual pick would).
    deck.cards[0].scryfall_id = cards["AART"].scryfall_id
    await session.commit()
    cov = await deck_coverage(session, deck, fmt="commander")
    # Legality follows the card (oracle), not the printing -> still legal.
    assert cov.illegal_count == 0 and cov.is_legal
    # ...but the row still shows the chosen printing for display (set codes are stored lowercased).
    assert cov.main[0].set_code == "aart"


@pytest.mark.asyncio
async def test_deck_printings_lists_playable_first(session):
    from src.decks import deck_printings
    oracle, _cards = await _seed_signet(session)
    prints = await deck_printings(session, uuid.UUID(oracle))
    assert prints[0]["playable"] is True
    assert prints[-1]["playable"] is False


def test_normalize_language():
    from src.decks import normalize_language
    assert normalize_language("JA") == "ja"
    assert normalize_language(None) == "en"
    assert normalize_language("klingon") == "en"


@pytest.mark.asyncio
async def test_update_deck_card_printing_language_and_flags(client, session):
    _oracle, cards = await _seed_signet(session)
    deck = await create_deck(session, "C", "1 Boros Signet")
    dc = deck.cards[0]
    resp = await client.post(
        f"/decks/{deck.id}/card/{dc.id}",
        data={"scryfall_id": str(cards["AART"].scryfall_id), "language": "JA",
              "proxy": "1", "special": "1", "format": "commander"},
    )
    assert resp.status_code == 204
    assert resp.headers["hx-redirect"] == f"/decks/{deck.id}?format=commander"
    await session.refresh(dc)
    assert str(dc.scryfall_id) == str(cards["AART"].scryfall_id)
    assert dc.proxy is True and dc.special is True and dc.language == "ja"


@pytest.mark.asyncio
async def test_update_deck_card_rejects_foreign_printing(client, session):
    _oracle, cards = await _seed_signet(session)
    other = Card(**card_to_columns(
        {"id": str(uuid.uuid4()), "oracle_id": str(uuid.uuid4()), "name": "Sol Ring",
         "set": "CMM", "collector_number": "1", "legalities": {"commander": "legal"}}
    ))
    session.add(other)
    await session.commit()
    deck = await create_deck(session, "C", "1 Boros Signet")
    dc = deck.cards[0]
    original = str(dc.scryfall_id)
    # A printing that belongs to a different card is ignored.
    resp = await client.post(
        f"/decks/{deck.id}/card/{dc.id}",
        data={"scryfall_id": str(other.scryfall_id), "language": "en"},
    )
    assert resp.status_code == 204
    await session.refresh(dc)
    assert str(dc.scryfall_id) == original


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
