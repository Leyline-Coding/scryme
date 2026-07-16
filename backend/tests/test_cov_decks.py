"""Coverage tests for src.decks: parsing edge cases, resolution fallbacks, edits, stats."""

import uuid

import pytest
from src.decks import (
    _is_playable,
    _legalities_by_oracle,
    _load_deck_printings,
    _merge_lines,
    _parse_deck_line,
    _resolve_names,
    add_card_to_deck,
    apply_deck_card_edit,
    create_deck,
    deck_coverage,
    deck_missing,
    deck_printings,
    deck_stats,
    normalize_language,
    parse_decklist,
)
from src.models import Card, CollectionCard, DeckCard
from src.scryfall.mapping import card_to_columns


def _raw(name, *, oracle=None, set_code="tst", cn="1", usd="1.00", legal=None,
         type_line="Instant", cmc=1, ci=None, layout="normal", released="2020-01-01"):
    return {"id": str(uuid.uuid4()), "oracle_id": oracle or str(uuid.uuid4()), "name": name,
            "set": set_code, "collector_number": str(cn), "rarity": "common", "cmc": cmc,
            "type_line": type_line, "colors": ci or [], "color_identity": ci or [],
            "layout": layout, "released_at": released, "prices": {"usd": usd},
            "legalities": legal or {"commander": "legal", "modern": "legal"}}


async def _add(session, raw, owned=0):
    c = Card(**card_to_columns(raw))
    session.add(c)
    await session.flush()
    if owned:
        session.add(CollectionCard(scryfall_id=c.scryfall_id, quantity=owned))
    await session.commit()
    return c


def test_parse_deck_line_none_and_empty():
    # No leading quantity -> no match -> None.
    assert _parse_deck_line("just a name", "main") is None
    # Name collapses to empty after stripping the foil marker.
    assert _parse_deck_line("5 *F*", "main") is None


def test_parse_decklist_skips_comments_and_headers():
    rows = parse_decklist("// a comment\n# hash\n\n2 Forest\nSideboard\n1 Duress\nnonsense line")
    assert (rows[0].name, rows[0].board) == ("Forest", "main")
    assert (rows[1].name, rows[1].board) == ("Duress", "side")
    assert len(rows) == 2


def test_parse_deck_line_sb_prefix():
    line = _parse_deck_line("SB: 3 Duress", "main")
    assert (line.quantity, line.name, line.board) == (3, "Duress", "side")


def test_merge_lines_bumps_duplicates():
    merged = _merge_lines(parse_decklist("2 Forest\n3 Forest\n1 Island"))
    by_name = {m.name: m.quantity for m in merged}
    assert by_name == {"Forest": 5, "Island": 1}


def test_is_playable():
    assert _is_playable({"modern": "legal", "commander": "not_legal"}) is True
    assert _is_playable({"modern": "not_legal", "commander": "not_legal"}) is False
    assert _is_playable(None) is False


def test_normalize_language_variants():
    assert normalize_language("  FR ") == "fr"
    assert normalize_language("") == "en"
    assert normalize_language("xx") == "en"


@pytest.mark.asyncio
async def test_resolve_names_empty_returns_empty(session):
    assert await _resolve_names(session, [], set()) == {}


@pytest.mark.asyncio
async def test_resolve_names_split_card_fallback(session):
    # A split / double-faced card resolves when only its front-face name is requested.
    await _add(session, _raw("Fire // Ice", cn="128"))
    resolved = await _resolve_names(session, ["Fire"], set())
    assert "fire" in resolved
    oracle, sid = resolved["fire"]
    assert oracle is not None and sid is not None


@pytest.mark.asyncio
async def test_add_card_to_deck_missing_deck(session):
    c = await _add(session, _raw("Sol Ring"))
    assert await add_card_to_deck(session, 99999, c) is False


@pytest.mark.asyncio
async def test_add_card_to_deck_new_then_bump(session):
    c = await _add(session, _raw("Sol Ring"))
    deck = await create_deck(session, "D", "")  # empty deck
    assert await add_card_to_deck(session, deck.id, c) is True
    await session.refresh(deck)
    assert len(deck.cards) == 1 and deck.cards[0].quantity == 1
    # Second add bumps the existing main-board line.
    assert await add_card_to_deck(session, deck.id, c) is True
    await session.refresh(deck)
    assert deck.cards[0].quantity == 2


@pytest.mark.asyncio
async def test_apply_deck_card_edit_paths(session):
    oracle = str(uuid.uuid4())
    a = await _add(session, _raw("Boros Signet", oracle=oracle, set_code="cmm", cn="1"))
    b = await _add(session, _raw("Boros Signet", oracle=oracle, set_code="aart", cn="5"))
    other = await _add(session, _raw("Sol Ring"))
    deck = await create_deck(session, "D", "1 Boros Signet")
    dc = deck.cards[0]

    # Invalid scryfall_id -> sid None, printing unchanged; flags + language applied.
    await apply_deck_card_edit(session, dc, scryfall_id="not-a-uuid", language="JA",
                               proxy=True, special=True)
    await session.refresh(dc)
    assert dc.language == "ja" and dc.proxy is True and dc.special is True

    # A printing belonging to a different card is rejected.
    await apply_deck_card_edit(session, dc, scryfall_id=str(other.scryfall_id))
    await session.refresh(dc)
    assert str(dc.scryfall_id) in {str(a.scryfall_id), str(b.scryfall_id)}

    # A valid sibling printing (same oracle) is accepted.
    await apply_deck_card_edit(session, dc, scryfall_id=str(b.scryfall_id))
    await session.refresh(dc)
    assert str(dc.scryfall_id) == str(b.scryfall_id)


@pytest.mark.asyncio
async def test_legalities_by_oracle_empty(session):
    assert await _legalities_by_oracle(session, set()) == {}
    assert await _legalities_by_oracle(session, {None}) == {}


@pytest.mark.asyncio
async def test_load_deck_printings_empty(session):
    price, prints, oracle_sid = await _load_deck_printings(session, [], "tcgplayer")
    assert price == {} and prints == {} and oracle_sid == {}


@pytest.mark.asyncio
async def test_deck_coverage_and_stats_and_missing(session):
    oracle_bolt = str(uuid.uuid4())
    bolt = await _add(session, _raw("Lightning Bolt", oracle=oracle_bolt, cn="1", usd="5.00",
                                    type_line="Instant", ci=["R"], cmc=1), owned=1)
    await _add(session, _raw("Forest", cn="2", usd="0.10",
                             type_line="Basic Land — Forest", cmc=0, ci=[]))
    deck = await create_deck(session, "Burn", "4 Lightning Bolt\n10 Forest\n1 Mystery")

    cov = await deck_coverage(session, deck, fmt="modern", currency="usd", source="tcgplayer")
    assert cov.total_needed == 15
    assert cov.owned_count == 1
    assert cov.unmatched == 1
    assert cov.fmt == "modern"

    # Stats: nonland mana curve + color pie + total value.
    stats = await deck_stats(session, deck, "usd", "tcgplayer")
    assert stats.has_data
    assert stats.total_value > 0

    # deck_missing skips the unmatched "Mystery" line, keeps Bolt+Forest.
    missing = await deck_missing(session, deck)
    names = {m.name: m.missing for m in missing}
    assert names == {"Lightning Bolt": 3, "Forest": 10}
    assert bolt is not None


@pytest.mark.asyncio
async def test_deck_missing_skips_unmatched_only(session):
    deck = await create_deck(session, "D", "1 TotallyUnknownCard")
    assert await deck_missing(session, deck) == []


@pytest.mark.asyncio
async def test_deck_stats_no_sids(session):
    # A deck of only-unmatched lines has no scryfall ids -> stats query is skipped.
    deck = await create_deck(session, "D", "1 UnknownA\n1 UnknownB")
    stats = await deck_stats(session, deck)
    assert not stats.has_data


@pytest.mark.asyncio
async def test_deck_printings_orders_playable_first(session):
    oracle = str(uuid.uuid4())
    await _add(session, _raw("Boros Signet", oracle=oracle, set_code="cmm", cn="1",
                             legal={"commander": "legal"}, released="2022-01-01"))
    await _add(session, _raw("Boros Signet", oracle=oracle, set_code="aart", cn="5",
                             legal={"commander": "not_legal"}, layout="art_series",
                             released="2099-01-01"))
    prints = await deck_printings(session, uuid.UUID(oracle))
    assert prints[0]["playable"] is True
    assert prints[-1]["playable"] is False


@pytest.mark.asyncio
async def test_coverage_properties_and_illegal_card(session):
    # Bolt legal in modern but not standard; covers pct_complete, is_legal, illegal legality branch.
    await _add(session, _raw("Lightning Bolt", cn="1",
                             legal={"modern": "legal", "standard": "not_legal"}), owned=0)
    deck = await create_deck(session, "D", "2 Lightning Bolt")
    legal = await deck_coverage(session, deck, fmt="modern")
    assert legal.pct_complete == 0  # own none
    illegal = await deck_coverage(session, deck, fmt="standard")
    assert illegal.illegal_count == 1 and illegal.is_legal is False


@pytest.mark.asyncio
async def test_deck_coverage_no_format(session):
    await _add(session, _raw("Forest", cn="9"), owned=0)
    deck = await create_deck(session, "D", "3 Forest")
    cov = await deck_coverage(session, deck)  # no fmt
    assert cov.fmt is None and cov.illegal_count == 0
    # DeckCard rows exist and land on the main board.
    assert isinstance(deck.cards[0], DeckCard)
