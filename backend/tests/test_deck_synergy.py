"""Deck-fit scoring: themes, deck-derived tribes, and how they re-rank upgrades (#294).

Legality and colour identity only make a pick *allowed*. These tests pin the part that makes it
*good*: that a card advancing the deck's plan outranks an equally legal generic one, and that a
deck with no detectable plan ranks exactly as it did before.
"""

import uuid
from collections import Counter

import pytest
from src.deck_suggest import suggest_owned_upgrades
from src.deck_synergy import (
    DeckProfile,
    card_synergy,
    deck_themes,
    deck_tribes,
    subtypes,
    synergy_note,
)
from src.decks import create_deck
from src.models import Card, CollectionCard
from src.scryfall.mapping import card_to_columns

# --- reading a type line -------------------------------------------------------------------------

def test_subtypes_reads_creature_types_only():
    assert subtypes("Legendary Creature — Elf Druid") == {"elf", "druid"}
    # Non-creature subtypes are not tribes: an Aura deck is not "Aura tribal".
    assert subtypes("Enchantment — Aura") == set()
    assert subtypes("Artifact") == set()
    assert subtypes(None) == set()
    assert subtypes("Creature") == set()          # no subtype at all
    assert "token" not in subtypes("Token Creature — Elf Token")


# --- tribes come from the deck, not a hardcoded list ---------------------------------------------

def test_a_tribe_the_commander_names_counts_immediately():
    """One Elf plus an Elf-caring commander is an Elf deck; five Elves shouldn't be required."""
    tribes = deck_tribes("Other Elves you control get +1/+1.", ["Creature — Elf Druid"])
    # "Druid" is on the same type line but the commander says nothing about Druids.
    assert tribes == {"elf"}


def test_a_tribe_the_deck_already_plays_in_bulk_counts():
    lines = ["Creature — Goblin"] * 5
    assert "goblin" in deck_tribes("", lines)
    assert deck_tribes("", ["Creature — Goblin"] * 4) == set()   # below the threshold


def test_tribe_matching_tolerates_the_plural_scryfall_writes():
    assert "elf" in deck_tribes("Elves you control have haste.", ["Creature — Elf"])
    assert "zombie" in deck_tribes("Create a Zombie token.", ["Creature — Zombie"])
    # A substring of a longer word is not a match.
    assert "elf" not in deck_tribes("Sacrifice a Shelf.", ["Creature — Elf"])


def test_irregular_plurals_the_real_creature_types_use():
    """Elves/Wolves/Dwarves and Allies — a naive "+s" misses exactly the popular tribes."""
    assert "wolf" in deck_tribes("Wolves you control have trample.", ["Creature — Wolf"])
    assert "dwarf" in deck_tribes("Dwarves you control get +1/+0.", ["Creature — Dwarf"])
    assert "ally" in deck_tribes("Allies you control gain vigilance.", ["Creature — Ally"])
    # The singular still matches, and so does a regular plural.
    assert "goblin" in deck_tribes("Target Goblin gains haste.", ["Creature — Goblin"])


def test_no_creatures_means_no_tribes():
    assert deck_tribes("Elves you control get +1/+1.", []) == set()


# --- themes --------------------------------------------------------------------------------------

def test_deck_themes_from_repeated_keywords_and_text_signals():
    kw = Counter({"Flying": 3, "Landfall": 2, "Trample": 1})
    texts = ["gain life"] * 4
    themes = deck_themes(kw, texts)
    assert "Flying" in themes and "Landfall" in themes
    assert "Trample" not in themes            # seen once
    assert "lifegain" in themes               # text signal in >= 4 cards


# --- scoring one card ----------------------------------------------------------------------------

def test_a_card_matching_a_theme_scores_and_explains_itself():
    profile = DeckProfile(themes=["tokens", "Flying"])
    score, reasons = card_synergy(
        profile, keywords=["Flying"], type_line="Creature — Bird",
        oracle_text="Create a 1/1 white Bird creature token.",
    )
    assert score == 2                          # one keyword + one text signal
    assert "flying" in reasons and "tokens" in reasons
    assert synergy_note(reasons) == "matches your flying, tokens"


def test_a_tribal_hit_outweighs_a_text_match():
    """Being the deck's tribe is its whole plan; mentioning a token is a mode."""
    profile = DeckProfile(themes=["tokens"], tribes={"elf"})
    tribal, _ = card_synergy(profile, keywords=None, type_line="Creature — Elf Warrior",
                             oracle_text="")
    textual, _ = card_synergy(profile, keywords=None, type_line="Creature — Bird",
                              oracle_text="Create a token.")
    assert tribal > textual


def test_an_unrelated_card_scores_nothing():
    profile = DeckProfile(themes=["tokens"], tribes={"elf"})
    score, reasons = card_synergy(profile, keywords=["Trample"],
                                  type_line="Creature — Bird", oracle_text="Flying.")
    assert score == 0 and reasons == [] and synergy_note(reasons) == ""


def test_an_empty_profile_scores_nothing_rather_than_noise():
    profile = DeckProfile()
    assert not profile.informative
    score, _ = card_synergy(profile, keywords=["Flying"], type_line="Creature — Elf",
                            oracle_text="Create a token. Gain life.")
    assert score == 0


def test_synergy_note_dedupes_and_caps():
    assert synergy_note(["tokens", "tokens", "elf", "sacrifice"]) == "matches your tokens, elf"
    assert synergy_note(["a", "b", "c"], limit=3) == "matches your a, b, c"


# --- end to end through the suggester -------------------------------------------------------------

async def _card(session, name, type_line, oracle_text="", *, keywords=None, cmc=2,
                owned=0, identity=("G",)):
    raw = {"id": str(uuid.uuid4()), "oracle_id": str(uuid.uuid4()), "name": name,
           "set": "TST", "collector_number": str(abs(hash(name)) % 9000),
           "type_line": type_line, "oracle_text": oracle_text, "cmc": cmc,
           "keywords": list(keywords or []), "color_identity": list(identity),
           "rarity": "rare", "legalities": {"commander": "legal"}, "prices": {"usd": "1.00"}}
    card = Card(**card_to_columns(raw))
    session.add(card)
    await session.flush()
    if owned:
        session.add(CollectionCard(scryfall_id=card.scryfall_id, quantity=owned))
    await session.commit()
    return card


async def _elf_deck(session):
    """A deck whose commander cares about Elves, and which is thin on ramp."""
    await _card(session, "Elf Lord", "Legendary Creature — Elf Druid",
                "Other Elves you control get +1/+1.", cmc=4)
    lines = ["1 Elf Lord"]
    for i in range(3):
        await _card(session, f"Deck Elf {i}", "Creature — Elf Warrior")
        lines.append(f"1 Deck Elf {i}")
    return await create_deck(session, "Elfball", "\n".join(lines))


@pytest.mark.asyncio
async def test_a_synergistic_ramp_card_outranks_a_generic_one(session):
    """Both are owned, in-identity, legal Ramp. Only one advances the deck's plan."""
    deck = await _elf_deck(session)
    await _card(session, "Generic Rock", "Artifact", "Add {G}.", cmc=2, owned=1)
    await _card(session, "Elf Mystic", "Creature — Elf Druid", "Add {G}.", cmc=2, owned=1)

    result = await suggest_owned_upgrades(session, deck)
    ramp = [p.name for p in result.by_role["Ramp"]]
    assert ramp[0] == "Elf Mystic", ramp
    pick = result.by_role["Ramp"][0]
    assert "elf" in pick.fit and pick.synergy > 0
    assert "elf" in result.themes


@pytest.mark.asyncio
async def test_synergy_beats_a_cheaper_lower_curve_generic(session):
    """Curve and price stay tie-breakers — they must not override actual deck fit."""
    deck = await _elf_deck(session)
    await _card(session, "Cheap Rock", "Artifact", "Add {G}.", cmc=1, owned=1)
    await _card(session, "Costly Elf", "Creature — Elf Druid", "Add {G}.", cmc=4, owned=1)

    ramp = [p.name for p in (await suggest_owned_upgrades(session, deck)).by_role["Ramp"]]
    assert ramp[:2] == ["Costly Elf", "Cheap Rock"]


@pytest.mark.asyncio
async def test_a_themeless_deck_still_ranks_by_curve_then_price(session):
    """No detectable plan -> no synergy signal -> the original ordering is unchanged."""
    await _card(session, "Plain Bear", "Creature — Bear", cmc=2)
    deck = await create_deck(session, "Pile", "1 Plain Bear")
    await _card(session, "Four Drop", "Artifact", "Add {G}.", cmc=4, owned=1)
    await _card(session, "One Drop", "Artifact", "Add {G}.", cmc=1, owned=1)

    result = await suggest_owned_upgrades(session, deck)
    ramp = [p.name for p in result.by_role["Ramp"]]
    assert ramp == ["One Drop", "Four Drop"]
    assert all(p.fit == "" for p in result.by_role["Ramp"])


@pytest.mark.asyncio
async def test_sideboard_cards_do_not_shape_the_profile(session):
    """A sideboard describes what the deck isn't doing."""
    lines = ["1 Plain Bear", "Sideboard"]
    await _card(session, "Plain Bear", "Creature — Bear")
    for i in range(6):
        await _card(session, f"Side Elf {i}", "Creature — Elf")
        lines.append(f"1 Side Elf {i}")
    deck = await create_deck(session, "Main matters", "\n".join(lines))

    result = await suggest_owned_upgrades(session, deck)
    assert "elf" not in result.themes
