"""AI suggestion grounding: fuller commander text, annotated candidates, fit-first shortlist (#295).

The complaint behind #295 is that the AI picks read as generic goodstuff. These tests pin the three
grounding defects that caused it — the model couldn't see the commander's whole ability, couldn't
see what a candidate *does*, and was shown an essentially arbitrary slice of the collection.
"""

import uuid

import pytest
from src.deck_synergy import DeckProfile
from src.decks import create_deck
from src.llm import _pool_cards, _snippet, deck_ai_context, suggest_from_collection
from src.models import Card, CollectionCard
from src.scryfall.mapping import card_to_columns


class FakeChat:
    """Captures the prompt it was given and replies with a fixed pick."""

    def __init__(self, reply=""):
        self.reply = reply
        self.messages = []

    async def chat(self, messages, **kw):
        self.messages = messages
        return self.reply

    @property
    def system(self) -> str:
        return self.messages[0]["content"]

    @property
    def user(self) -> str:
        return self.messages[1]["content"]


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


_MULTILINE_COMMANDER = (
    "Vigilance, trample.\n"
    "Whenever a creature you control dies, put a +1/+1 counter on Grave Captain.\n"
    "{2}{G}: Return target Elf card from your graveyard to your hand."
)


async def _deck(session):
    await _card(session, "Grave Captain", "Legendary Creature — Elf Warrior",
                _MULTILINE_COMMANDER, cmc=4)
    return await create_deck(session, "Graveyard Elves", "1 Grave Captain")


# --- the commander's whole ability is grounded ----------------------------------------------------

@pytest.mark.asyncio
async def test_the_commanders_full_text_reaches_the_prompt(session):
    """The engine is on lines two and three; truncating to line one hid the deck's plan."""
    deck = await _deck(session)
    ctx = await deck_ai_context(session, deck)

    assert "+1/+1 counter" in ctx.commander_text        # line 2
    assert "from your graveyard" in ctx.commander_text  # line 3
    assert "Vigilance" in ctx.commander_text            # line 1 still there
    assert "\n" not in ctx.commander_text               # kept to one line in the block
    assert "+1/+1 counter" in ctx.block()


@pytest.mark.asyncio
async def test_commander_text_is_bounded(session):
    """A pathological wall of text must not crowd out the rest of the prompt."""
    await _card(session, "Wordy One", "Legendary Creature — Elf", "word " * 500, cmc=5)
    deck = await create_deck(session, "Wordy", "1 Wordy One")
    ctx = await deck_ai_context(session, deck)
    assert 0 < len(ctx.commander_text) <= 600


@pytest.mark.asyncio
async def test_the_decks_creature_types_are_grounded(session):
    """The commander names Elves, so Elf is part of the plan and the model is told so."""
    deck = await _deck(session)
    ctx = await deck_ai_context(session, deck)
    assert "elf" in ctx.tribes
    assert "Creature types this deck cares about" in ctx.block()


@pytest.mark.asyncio
async def test_a_commander_that_merely_is_an_elf_does_not_make_an_elf_deck(session):
    """Plenty of commanders have a creature type they say nothing about — that isn't a theme."""
    await _card(session, "Plain Legend", "Legendary Creature — Elf Warrior", "Vigilance.", cmc=3)
    deck = await create_deck(session, "Not tribal", "1 Plain Legend")
    assert (await deck_ai_context(session, deck)).tribes == []


# --- candidates say what they do ------------------------------------------------------------------

def test_snippet_collapses_and_truncates():
    assert _snippet("Add  {G}.\nDraw a card.") == "Add {G}. Draw a card."
    long = _snippet("x" * 300)
    assert long.endswith("…") and len(long) <= 111
    assert _snippet(None) == ""


@pytest.mark.asyncio
async def test_a_candidate_line_carries_role_and_what_the_card_does(session):
    deck = await _deck(session)
    await _card(session, "Elf Mystic", "Creature — Elf Druid", "Add {G}.", owned=1)

    cards = await _pool_cards(session, deck)
    line = next(c for c in cards if c.name == "Elf Mystic").line()
    assert line.startswith("Elf Mystic — ")
    assert "Ramp" in line and "Add {G}." in line


@pytest.mark.asyncio
async def test_a_candidate_line_names_the_keyword_it_shares_with_the_deck(session):
    """Only keywords the deck actually cares about are worth prompt space."""
    await _card(session, "Grave Captain", "Legendary Creature — Elf Warrior",
                _MULTILINE_COMMANDER, cmc=4)
    for i in range(3):
        await _card(session, f"Flier {i}", "Creature — Bird", "", keywords=["Flying"])
    deck = await create_deck(session, "Skies",
                             "1 Grave Captain\n" + "\n".join(f"1 Flier {i}" for i in range(3)))
    await _card(session, "Owned Flier", "Creature — Bird", "Attacks.", keywords=["Flying"],
                owned=1)
    await _card(session, "Owned Ground", "Creature — Bear", "Attacks.", keywords=["Trample"],
                owned=1)

    ctx = await deck_ai_context(session, deck)
    profile = DeckProfile(themes=ctx.themes, tribes=set(ctx.tribes))
    lines = {c.name: c.line() for c in await _pool_cards(session, deck, profile)}
    assert "Flying" in lines["Owned Flier"]
    assert "Trample" not in lines["Owned Ground"]   # not a theme of this deck


@pytest.mark.asyncio
async def test_the_prompt_annotates_candidates_and_demands_specific_synergy(session):
    deck = await _deck(session)
    await _card(session, "Elf Mystic", "Creature — Elf Druid", "Add {G}.", owned=1)
    fake = FakeChat("Elf Mystic - ramps into the commander")

    await suggest_from_collection(session, deck, fake)
    assert "Elf Mystic — Ramp | Add {G}." in fake.user
    assert "commander's specific ability" in fake.system
    assert "generic staples" in fake.system


# --- the shortlist is chosen by fit, not by row order ---------------------------------------------

@pytest.mark.asyncio
async def test_the_shortlist_prefers_cards_that_fit_the_deck(session):
    """With more owned cards than fit in the prompt, the synergistic ones must be the ones sent."""
    deck = await _deck(session)
    for i in range(60):
        await _card(session, f"Generic Bear {i:02d}", "Creature — Bear", "Vanilla.", owned=1)
    await _card(session, "Elfish Ally", "Creature — Elf Shaman", "Add {G}.", owned=1)

    fake = FakeChat("")
    await suggest_from_collection(session, deck, fake)
    assert "Elfish Ally" in fake.user
    # The shortlist is capped, so the generics can't all be present — that's the point.
    assert fake.user.count("Generic Bear") < 60


@pytest.mark.asyncio
async def test_validation_still_covers_the_whole_pool_not_just_the_shortlist(session):
    """A named card must resolve even if it wasn't among the 40 sent — not a "hallucination"."""
    deck = await _deck(session)
    for i in range(60):
        await _card(session, f"Generic Bear {i:02d}", "Creature — Bear", "Vanilla.", owned=1)
    await _card(session, "Obscure Pick", "Creature — Bear", "Vanilla.", owned=1)

    fake = FakeChat("Obscure Pick - fills a hole")
    result = await suggest_from_collection(session, deck, fake)
    assert [s.name for s in result.suggestions] == ["Obscure Pick"]
    assert result.considered > 40


@pytest.mark.asyncio
async def test_no_owned_candidates_short_circuits(session):
    deck = await _deck(session)
    fake = FakeChat("")
    result = await suggest_from_collection(session, deck, fake)
    assert result.considered == 0 and result.suggestions == []
    assert fake.messages == []   # no call made
