"""Build-a-deck-from-collection (#87): role classification + the commander build."""

import uuid

import pytest
from src.deck_builder import BuildError, build_commander_deck, classify_role
from src.models import Card, CollectionCard


def test_classify_role():
    assert classify_role("Basic Land — Forest", "") == "Lands"
    assert classify_role("Creature — Elf Druid", "{T}: Add {G}.") == "Ramp"
    assert classify_role("Sorcery", "Search your library for a basic land card.") == "Ramp"
    assert classify_role("Instant", "Destroy target creature.") == "Removal"
    assert classify_role("Sorcery", "Each player draws two cards.") == "Card draw"
    assert classify_role("Creature — Beast", "Trample") == "Creatures"
    assert classify_role("Enchantment", "Whenever...") == "Other"


async def _own(session, name, *, identity, type_line, text="", cmc=2.0, commander="legal"):
    card = Card(
        scryfall_id=uuid.uuid4(),
        oracle_id=uuid.uuid4(),
        name=name,
        set_code="tst",
        collector_number="1",
        color_identity=identity,
        type_line=type_line,
        oracle_text=text,
        cmc=cmc,
        legalities={"commander": commander},
        raw={"name": name},
    )
    session.add(card)
    await session.flush()
    session.add(CollectionCard(scryfall_id=card.scryfall_id, quantity=1))
    return card


@pytest.mark.asyncio
async def test_build_commander_deck(session):
    await _own(session, "Gruff General", identity=["G"], type_line="Legendary Creature — Bear")
    await _own(session, "Llanowar Elves", identity=["G"], type_line="Creature — Elf",
               text="{T}: Add {G}.")
    await _own(session, "Beast Within", identity=["G"], type_line="Instant",
               text="Destroy target permanent.")
    await _own(session, "Harmonize", identity=["G"], type_line="Sorcery",
               text="Draw three cards.")
    await _own(session, "Grizzly Bears", identity=["G"], type_line="Creature — Bear",
               text="vanilla")
    # Out of colour identity (blue) — must be excluded from a mono-green deck.
    await _own(session, "Blue Drake", identity=["U"], type_line="Creature — Drake")
    # Not Commander-legal — excluded.
    await _own(session, "Banned Thing", identity=["G"], type_line="Creature — Horror",
               commander="banned")
    await session.commit()

    built = await build_commander_deck(session, "gruff general")
    assert built.commander == "Gruff General"
    assert built.identity == ["G"]

    names = [n for g in built.groups for n in g.cards]
    assert "Llanowar Elves" in names and "Beast Within" in names and "Harmonize" in names
    assert "Blue Drake" not in names and "Banned Thing" not in names
    # Few owned lands → basics top up the mana base, and Forest is the mono-green basic.
    assert built.basics_added > 0
    assert "1 Gruff General" == built.decklist_text.splitlines()[0]
    assert "Forest" in built.decklist_text


@pytest.mark.asyncio
async def test_build_rejects_non_commander(session):
    await _own(session, "Just A Bear", identity=["G"], type_line="Creature — Bear")
    await session.commit()
    with pytest.raises(BuildError):
        await build_commander_deck(session, "Just A Bear")  # not legendary


@pytest.mark.asyncio
async def test_build_unknown_name(session):
    with pytest.raises(BuildError):
        await build_commander_deck(session, "Nonexistent Commander")


@pytest.mark.asyncio
async def test_build_routes(client, session):
    await _own(session, "Solo Commander", identity=["R"], type_line="Legendary Creature — Goblin")
    await session.commit()

    assert (await client.get("/decks/build")).status_code == 200
    resp = await client.post("/decks/build", data={"commander": "Solo Commander"})
    assert resp.status_code == 200
    assert "Suggested deck" in resp.text and "Solo Commander" in resp.text
