"""Commander bracket estimator tests (#159)."""

import uuid

import pytest
from src.brackets import (
    BRACKET_LABELS,
    Signal,
    _CardInfo,
    estimate_bracket,
    score_bracket,
)
from src.decks import create_deck
from src.models import Card, CollectionCard
from src.scryfall.mapping import card_to_columns


def _ci(name, type_line="Creature", oracle_text="", game_changer=False):
    return _CardInfo(name=name, type_line=type_line, oracle_text=oracle_text,
                     game_changer=game_changer)


def _commander(cards):
    """Prepend a legendary creature so the estimate registers as a Commander deck."""
    return [_ci("Some General", "Legendary Creature — Human", "")] + list(cards)


def test_vanilla_precon_is_bracket_2():
    est = score_bracket(_commander([_ci("Grizzly Bears"),
                                    _ci("Divination", oracle_text="Draw two cards.")]))
    assert est.is_commander is True
    assert est.bracket == 2
    assert est.label == BRACKET_LABELS[2]
    assert est.signals == []


def test_game_changers_set_the_floor():
    one = score_bracket(_commander([_ci("Rhystic Study", game_changer=True)]))
    assert one.bracket == 3  # >=1 game changer -> Upgraded
    several = score_bracket(_commander([_ci(f"GC {i}", game_changer=True) for i in range(4)]))
    assert several.bracket == 4  # >=4 game changers -> Optimized
    labels = [s.label for s in several.signals]
    assert "Game Changers" in labels


def test_mass_land_denial_forces_four():
    est = score_bracket(_commander([_ci("Armageddon", "Sorcery", "Destroy all lands.")]))
    assert est.bracket == 4
    assert any(s.label == "Mass land denial" for s in est.signals)
    # Text signal alone also trips it (unknown name, MLD wording).
    text_only = score_bracket(_commander([_ci("Homebrew Wrath", "Sorcery", "Destroy all lands.")]))
    assert text_only.bracket == 4


def test_extra_turn_chaining_needs_two():
    single = score_bracket(_commander([_ci("Time Warp", "Sorcery",
                                            "Take an extra turn after this one.")]))
    assert single.bracket == 2  # one extra-turn spell is fine at Core
    assert any(s.label == "Extra turns" for s in single.signals)
    chain = score_bracket(_commander([
        _ci("Time Warp", "Sorcery", "Take an extra turn after this one."),
        _ci("Temporal Manipulation", "Sorcery", "Take an extra turn after this one."),
    ]))
    assert chain.bracket == 4
    assert any(s.label == "Extra-turn chaining" for s in chain.signals)


def test_combo_pair_needs_both_halves():
    one = score_bracket(_commander([_ci("Thassa's Oracle")]))
    assert one.bracket == 2  # only one combo piece present
    both = score_bracket(_commander([_ci("Thassa's Oracle"),
                                     _ci("Demonic Consultation", oracle_text="")]))
    assert both.bracket == 4
    combo_sig = next(s for s in both.signals if s.label == "Possible infinite combo")
    assert "Thassa's Oracle + Demonic Consultation" in combo_sig.detail


def test_fast_mana_and_tutors_bump_to_three():
    fast = score_bracket(_commander([_ci("Mana Crypt"), _ci("Mana Vault")]))
    assert fast.bracket == 3  # >=2 fast mana nudges a baseline deck up
    tutors = score_bracket(_commander([
        _ci(f"Tutor {i}", "Sorcery", "Search your library for a card, then shuffle.")
        for i in range(3)
    ]))
    assert tutors.bracket == 3  # >=3 tutors nudges up
    assert any(s.label == "Tutors" for s in tutors.signals)
    # Sol Ring is intentionally not fast mana, and a lone tutor doesn't bump.
    lone = score_bracket(_commander([
        _ci("Sol Ring", "Artifact", "Add {C}{C}."),
        _ci("Demonic Tutor", "Sorcery", "Search your library for a card, then shuffle."),
    ]))
    assert lone.bracket == 2


def test_land_fetch_is_not_a_tutor():
    est = score_bracket(_commander([
        _ci("Evolving Wilds", "Land", "Search your library for a basic land card."),
        _ci("Farseek", "Sorcery", "Search your library for a Plains or Island card."),
    ]))
    assert not any(s.label == "Tutors" for s in est.signals)
    assert est.bracket == 2


def test_non_commander_deck_is_flagged():
    est = score_bracket([_ci("Lightning Bolt", "Instant", "Deal 3 damage.")])
    assert est.is_commander is False


def test_planeswalker_commander_detected_via_text():
    est = score_bracket([
        _ci("The Wandering Emperor", "Legendary Planeswalker — The Wandering Emperor",
            "The Wandering Emperor can be your commander."),
    ])
    assert est.is_commander is True


def test_bracket_capped_at_four():
    loaded = score_bracket(_commander(
        [_ci(f"GC {i}", game_changer=True) for i in range(6)]
        + [_ci("Armageddon", "Sorcery", "Destroy all lands.")]
        + [_ci("Mana Crypt")]
    ))
    assert loaded.bracket == 4  # never exceeds the heuristic cap


def test_signal_is_a_dataclass_with_weight():
    s = Signal("Game Changers", "1: Rhystic Study", 2)
    assert (s.label, s.detail, s.weight) == ("Game Changers", "1: Rhystic Study", 2)


async def _seed(session, raw_overrides):
    """Insert cards from raw dicts and own one copy of each; return them by name."""
    cards = {}
    for raw in raw_overrides:
        c = Card(**card_to_columns(raw))
        session.add(c)
        cards[raw["name"]] = c
    await session.flush()
    for c in cards.values():
        session.add(CollectionCard(scryfall_id=c.scryfall_id, quantity=1))
    await session.commit()
    return cards


def _raw(name, type_line, oracle_text="", game_changer=False, **extra):
    base = {"id": str(uuid.uuid4()), "oracle_id": str(uuid.uuid4()), "name": name,
            "set": "TST", "collector_number": "1", "type_line": type_line,
            "oracle_text": oracle_text, "color_identity": ["U"], "cmc": 2,
            "legalities": {"commander": "legal"}}
    if game_changer:
        base["game_changer"] = True
    base.update(extra)
    return base


@pytest.mark.asyncio
async def test_estimate_bracket_reads_game_changer_from_raw(session):
    await _seed(session, [
        _raw("Some General", "Legendary Creature — Merfolk"),
        _raw("Rhystic Study", "Enchantment", "Whenever an opponent casts a spell...",
             game_changer=True),
    ])
    deck = await create_deck(session, "EDH", "1 Some General\n1 Rhystic Study")
    est = await estimate_bracket(session, deck)
    assert est.is_commander is True
    assert est.bracket == 3
    assert any(s.label == "Game Changers" for s in est.signals)


@pytest.mark.asyncio
async def test_estimate_bracket_empty_deck(session):
    deck = await create_deck(session, "Empty", "")
    est = await estimate_bracket(session, deck)
    assert est.is_commander is False
    assert est.bracket == 2


@pytest.mark.asyncio
async def test_deck_page_shows_bracket_for_commander(client, session):
    await _seed(session, [
        _raw("Kenrith, the Returned King", "Legendary Creature — Human Noble"),
        _raw("Armageddon", "Sorcery", "Destroy all lands.", color_identity=["W"]),
    ])
    deck = await create_deck(session, "EDH",
                             "1 Kenrith, the Returned King\n1 Armageddon")
    resp = await client.get(f"/decks/{deck.id}")
    assert resp.status_code == 200
    assert "Commander bracket" in resp.text
    assert "Mass land denial" in resp.text


@pytest.mark.asyncio
async def test_deck_page_hides_bracket_for_non_commander(client, session):
    await _seed(session, [_raw("Lightning Bolt", "Instant", "Deal 3 damage.",
                               color_identity=["R"])])
    deck = await create_deck(session, "Burn", "1 Lightning Bolt")
    resp = await client.get(f"/decks/{deck.id}")
    assert resp.status_code == 200
    assert "Commander bracket" not in resp.text


@pytest.mark.asyncio
async def test_deck_page_shows_bracket_when_commander_format_selected(client, session):
    # No legendary creature, but the user asserts Commander via the legality dropdown.
    await _seed(session, [_raw("Sol Ring", "Artifact", "Add {C}{C}.", color_identity=[])])
    deck = await create_deck(session, "Pile", "1 Sol Ring")
    resp = await client.get(f"/decks/{deck.id}?format=commander")
    assert resp.status_code == 200
    assert "Commander bracket" in resp.text
