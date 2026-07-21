"""Heuristic owned-card upgrade suggestion tests (#181)."""

import uuid

import pytest
from src.config import get_settings
from src.deck_suggest import suggest_owned_upgrades
from src.decks import create_deck
from src.models import Card, CollectionCard
from src.scryfall.mapping import card_to_columns


def _raw(name, type_line, oracle_text="", cmc=2, ci=("G",), usd="1.00",
         commander="legal", **extra):
    base = {"id": str(uuid.uuid4()), "oracle_id": str(uuid.uuid4()), "name": name,
            "set": "TST", "collector_number": "1", "type_line": type_line,
            "oracle_text": oracle_text, "cmc": cmc, "color_identity": list(ci),
            "prices": {"usd": usd}, "legalities": {"commander": commander}}
    base.update(extra)
    return base


async def _add_card(session, raw, owned=True):
    c = Card(**card_to_columns(raw))
    session.add(c)
    await session.flush()
    if owned:
        session.add(CollectionCard(scryfall_id=c.scryfall_id, quantity=1))
    await session.commit()
    return c


# A green ramp candidate the collection owns and the deck doesn't run.
_RAMP = lambda name="Cultivate", usd="1.00", cmc=3: _raw(  # noqa: E731
    name, "Sorcery", "Search your library for up to two basic land cards...", cmc=cmc, usd=usd)


@pytest.mark.asyncio
async def test_suggests_owned_in_color_ramp_for_thin_deck(session):
    # Deck: a green commander only -> Ramp is thin (0 of ~10).
    cmd = _raw("Green General", "Legendary Creature — Elf", cmc=4)
    await _add_card(session, cmd)
    ramp = _RAMP()
    await _add_card(session, ramp)  # owned, not in deck
    deck = await create_deck(session, "EDH", "1 Green General")

    result = await suggest_owned_upgrades(session, deck)
    assert not result.empty
    names = [p.name for p in result.picks]
    assert "Cultivate" in names
    assert result.by_role["Ramp"][0].reason.startswith("Fills thin Ramp")
    assert result.considered >= 1


@pytest.mark.asyncio
async def test_excludes_off_color_and_illegal_and_in_deck(session):
    cmd = _raw("Green General", "Legendary Creature — Elf", cmc=4)
    await _add_card(session, cmd)
    off_color = _raw("Blue Ramp", "Sorcery", "Add {U}{U}.", ci=("U",))
    illegal = _raw("Banned Ramp", "Sorcery", "Add {G}{G}.", commander="banned")
    in_deck = _RAMP("Rampant Growth")
    await _add_card(session, off_color)
    await _add_card(session, illegal)
    await _add_card(session, in_deck)
    deck = await create_deck(session, "EDH", "1 Green General\n1 Rampant Growth")

    result = await suggest_owned_upgrades(session, deck)
    names = [p.name for p in result.picks]
    assert "Blue Ramp" not in names       # off the deck's identity
    assert "Banned Ramp" not in names     # not commander-legal
    assert "Rampant Growth" not in names  # already in the deck


@pytest.mark.asyncio
async def test_unowned_cards_are_not_suggested(session):
    cmd = _raw("Green General", "Legendary Creature — Elf", cmc=4)
    await _add_card(session, cmd)
    await _add_card(session, _RAMP("Kodama's Reach"), owned=False)  # in DB but not owned
    deck = await create_deck(session, "EDH", "1 Green General")
    result = await suggest_owned_upgrades(session, deck)
    assert "Kodama's Reach" not in [p.name for p in result.picks]


@pytest.mark.asyncio
async def test_ranking_prefers_lower_curve_then_cheaper(session):
    cmd = _raw("Green General", "Legendary Creature — Elf", cmc=4)
    await _add_card(session, cmd)
    await _add_card(session, _RAMP("Curve 5 Cheap", cmc=5, usd="0.10"))
    await _add_card(session, _RAMP("Curve 1 Pricey", cmc=1, usd="9.00"))
    await _add_card(session, _RAMP("Curve 1 Cheap", cmc=1, usd="5.00"))
    deck = await create_deck(session, "EDH", "1 Green General")

    ramp = (await suggest_owned_upgrades(session, deck)).by_role["Ramp"]
    # Lower cmc first; among equal cmc, cheaper first.
    assert ramp[0].name == "Curve 1 Cheap"
    assert ramp[1].name == "Curve 1 Pricey"
    assert ramp[2].name == "Curve 5 Cheap"


@pytest.mark.asyncio
async def test_well_stocked_role_is_not_suggested(session):
    cmd = _raw("Green General", "Legendary Creature — Elf", cmc=4)
    await _add_card(session, cmd)
    # Give the deck 10 ramp spells (meets the target) plus one owned spare.
    lines = ["1 Green General"]
    for i in range(10):
        r = _RAMP(f"Deck Ramp {i}")
        await _add_card(session, r, owned=False)
        lines.append(f"1 Deck Ramp {i}")
    await _add_card(session, _RAMP("Spare Ramp"))
    deck = await create_deck(session, "EDH", "\n".join(lines))

    result = await suggest_owned_upgrades(session, deck)
    assert "Ramp" not in result.by_role  # role already at target -> no suggestions


@pytest.mark.asyncio
async def test_empty_when_no_candidates(session):
    cmd = _raw("Green General", "Legendary Creature — Elf", cmc=4)
    await _add_card(session, cmd)
    deck = await create_deck(session, "EDH", "1 Green General")
    result = await suggest_owned_upgrades(session, deck)
    assert result.empty
    assert result.picks == []


@pytest.mark.asyncio
async def test_sideboard_line_and_nontunable_owned_ignored(session):
    cmd = _raw("Green General", "Legendary Creature — Elf", cmc=4)
    await _add_card(session, cmd)
    await _add_card(session, _raw("Forest", "Basic Land — Forest", "", cmc=0))  # non-tunable
    await _add_card(session, _RAMP())
    # A sideboard line exercises the board != "main" skip in the role counter.
    deck = await create_deck(session, "EDH", "1 Green General\nSideboard\n1 Unknown SB Card")
    result = await suggest_owned_upgrades(session, deck)
    names = [p.name for p in result.picks]
    assert "Cultivate" in names
    assert "Forest" not in names  # a land is never a ramp/draw/removal suggestion


@pytest.mark.asyncio
async def test_empty_deck_scores_all_roles_thin(session):
    # No mainboard cards -> role counts are all zero and identity is unconstrained.
    await _add_card(session, _RAMP())
    deck = await create_deck(session, "Empty", "")
    result = await suggest_owned_upgrades(session, deck)
    assert "Cultivate" in [p.name for p in result.picks]


# --- routes ------------------------------------------------------------------

@pytest.mark.asyncio
async def test_suggest_owned_route_renders(client, session):
    cmd = _raw("Green General", "Legendary Creature — Elf", cmc=4)
    await _add_card(session, cmd)
    await _add_card(session, _RAMP())
    deck = await create_deck(session, "EDH", "1 Green General")
    resp = await client.post(f"/decks/{deck.id}/suggest-owned")
    assert resp.status_code == 200
    assert "Cultivate" in resp.text
    assert "+ Add" in resp.text
    assert (await client.post("/decks/99999/suggest-owned")).status_code == 404


@pytest.mark.asyncio
async def test_add_owned_route_adds_and_refreshes(client, session):
    cmd = _raw("Green General", "Legendary Creature — Elf", cmc=4)
    await _add_card(session, cmd)
    ramp = await _add_card(session, _RAMP())
    deck = await create_deck(session, "EDH", "1 Green General")

    resp = await client.post(f"/decks/{deck.id}/add-owned",
                             data={"scryfall_id": str(ramp.scryfall_id)})
    assert resp.status_code == 200
    assert "Added Cultivate" in resp.text
    # Re-suggesting no longer offers it (now in the deck).
    again = await client.post(f"/decks/{deck.id}/suggest-owned")
    assert "Cultivate" not in again.text


@pytest.mark.asyncio
async def test_add_owned_ignores_bad_id(client, session):
    cmd = _raw("Green General", "Legendary Creature — Elf", cmc=4)
    await _add_card(session, cmd)
    await _add_card(session, _RAMP())
    deck = await create_deck(session, "EDH", "1 Green General")
    resp = await client.post(f"/decks/{deck.id}/add-owned", data={"scryfall_id": "not-a-uuid"})
    assert resp.status_code == 200
    assert "Added" not in resp.text  # nothing added, panel still renders
    assert (await client.post(f"/decks/{deck.id}/add-owned",
                              data={"scryfall_id": str(uuid.uuid4())})).status_code == 200
    # A missing deck 404s.
    assert (await client.post("/decks/99999/add-owned",
                              data={"scryfall_id": str(uuid.uuid4())})).status_code == 404


@pytest.mark.asyncio
async def test_add_owned_blocked_read_only(client, session, monkeypatch):
    cmd = _raw("Green General", "Legendary Creature — Elf", cmc=4)
    await _add_card(session, cmd)
    ramp = await _add_card(session, _RAMP())
    deck = await create_deck(session, "EDH", "1 Green General")
    monkeypatch.setattr(get_settings(), "read_only", True)
    resp = await client.post(f"/decks/{deck.id}/add-owned",
                             data={"scryfall_id": str(ramp.scryfall_id)})
    assert resp.status_code == 403
