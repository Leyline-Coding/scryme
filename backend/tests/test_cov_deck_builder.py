"""Coverage tests for src.deck_builder: owned_commanders, colourless basics, leftover top-up."""

import uuid

import pytest
import pytest as _pytest
from src.deck_builder import BuildError, build_commander_deck, classify_role, owned_commanders
from src.models import Card, CollectionCard


def test_classify_role_buckets():
    assert classify_role("Basic Land — Forest", "") == "Lands"
    assert classify_role("Sorcery", "Search your library for a basic land card.") == "Ramp"
    assert classify_role("Creature", "{T}: Add {G}.") == "Ramp"
    assert classify_role("Instant", "Destroy target creature.") == "Removal"
    assert classify_role("Sorcery", "Deals 3 damage to any target.") == "Removal"
    assert classify_role("Sorcery", "Each player draws two cards.") == "Card draw"
    assert classify_role("Creature — Beast", "Trample") == "Creatures"
    assert classify_role("Enchantment", "Nothing special") == "Other"
    assert classify_role(None, None) == "Other"


async def _own(session, name, *, identity, type_line, text="", cmc=2.0, commander="legal"):
    card = Card(scryfall_id=uuid.uuid4(), oracle_id=uuid.uuid4(), name=name, set_code="tst",
                collector_number=str(abs(hash(name)) % 9999), color_identity=identity,
                type_line=type_line, oracle_text=text, cmc=cmc,
                legalities={"commander": commander}, raw={"name": name})
    session.add(card)
    await session.flush()
    session.add(CollectionCard(scryfall_id=card.scryfall_id, quantity=1))
    return card


@pytest.mark.asyncio
async def test_owned_commanders_lists_legendary_creatures(session):
    await _own(session, "Big Boss", identity=["R"], type_line="Legendary Creature — Goblin")
    await _own(session, "Just A Bear", identity=["G"], type_line="Creature — Bear")
    await session.commit()
    names = await owned_commanders(session)
    assert names == ["Big Boss"]  # only the legendary creature


@pytest.mark.asyncio
async def test_colorless_commander_no_basics_and_land_shortfall(session):
    # Colourless commander -> _basics_for returns [] (no coloured basics) and lands fall short.
    await _own(session, "Karn, Silver Golem", identity=[],
               type_line="Legendary Artifact Creature — Golem")
    await session.commit()
    built = await build_commander_deck(session, "Karn, Silver Golem")
    assert built.identity == []
    assert built.basics_added == 0
    assert any(s.startswith("Lands") for s in built.shortfalls)


@pytest.mark.asyncio
async def test_build_full_mono_color_and_rejections(session):
    await _own(session, "Gruff General", identity=["G"], type_line="Legendary Creature — Bear")
    await _own(session, "Llanowar Elves", identity=["G"], type_line="Creature — Elf",
               text="{T}: Add {G}.")
    await _own(session, "Beast Within", identity=["G"], type_line="Instant",
               text="Destroy target permanent.")
    # Out-of-identity + non-commander-legal (in-identity) cards are excluded from the pool.
    await _own(session, "Blue Drake", identity=["U"], type_line="Creature — Drake")
    await _own(session, "Banned Thing", identity=["G"], type_line="Creature — Horror",
               commander="banned")
    await session.commit()
    built = await build_commander_deck(session, "gruff general")
    names = [n for g in built.groups for n in g.cards]
    assert "Llanowar Elves" in names and "Beast Within" in names
    assert "Blue Drake" not in names and "Banned Thing" not in names
    assert built.basics_added > 0 and "Forest" in built.decklist_text
    assert built.total >= 1

    with _pytest.raises(BuildError):
        await build_commander_deck(session, "Nonexistent")
    await _own(session, "Just A Bear", identity=["G"], type_line="Creature — Bear")
    await session.commit()
    with _pytest.raises(BuildError):
        await build_commander_deck(session, "Just A Bear")  # not legendary


@pytest.mark.asyncio
async def test_leftover_spells_topped_into_other(session):
    await _own(session, "Karn, Silver Golem", identity=[],
               type_line="Legendary Artifact Creature — Golem")
    # 8 colourless "Other" cards -> role target for "Other" is 6, leaving 2 leftovers that get
    # folded back into the "Other" group to top up the non-land count.
    for i in range(8):
        await _own(session, f"Trinket {i}", identity=[], type_line="Artifact",
                   text="Whenever something happens, do a thing.", cmc=float(i))
    await session.commit()
    built = await build_commander_deck(session, "Karn, Silver Golem")
    other_group = next(g for g in built.groups if g.name == "Other")
    assert len(other_group.cards) > 6  # leftovers folded in
