"""Deck export (text/Arena/Moxfield/MTGO) + per-deck stats."""

import uuid

import pytest
from src.deck_export import DeckExportCard, collect_export_cards, render_deck
from src.decks import create_deck, deck_stats
from src.models import Card
from src.scryfall.mapping import card_to_columns


def _cards():
    return [
        DeckExportCard("Lightning Bolt", 4, "main", "lea", "161"),
        DeckExportCard("Mountain", 20, "main", "mh2", "491"),
        DeckExportCard("MysteryCard", 1, "main", None, None),  # unresolved
        DeckExportCard("Pyroblast", 2, "side", "ice", "213"),
    ]


def test_render_text_is_plain_with_sideboard():
    out = render_deck(_cards(), "text")
    assert out.startswith("4 Lightning Bolt\n20 Mountain\n1 MysteryCard\n")
    assert "\nSideboard\n2 Pyroblast\n" in out
    assert "(LEA)" not in out  # plain text carries no set info


def test_render_arena_annotates_set_and_number():
    out = render_deck(_cards(), "arena")
    assert "4 Lightning Bolt (LEA) 161" in out
    assert "1 MysteryCard\n" in out  # unresolved card falls back to plain
    assert "\nSideboard\n2 Pyroblast (ICE) 213\n" in out


def test_render_moxfield_uses_sideboard_marker():
    out = render_deck(_cards(), "moxfield")
    assert "20 Mountain (MH2) 491" in out
    assert "SIDEBOARD:" in out and "Sideboard\n" not in out


def test_render_mtgo_is_dek_xml():
    out = render_deck(_cards(), "mtgo")
    assert out.startswith('<?xml version="1.0" encoding="utf-8"?>')
    assert '<Cards CatID="0" Quantity="4" Sideboard="false" Name="Lightning Bolt" />' in out
    assert 'Quantity="2" Sideboard="true" Name="Pyroblast"' in out
    assert out.strip().endswith("</Deck>")


def test_render_unknown_format_falls_back_to_text():
    assert render_deck(_cards(), "bogus") == render_deck(_cards(), "text")


async def _seed(session):
    bolt = {"id": str(uuid.uuid4()), "oracle_id": str(uuid.uuid4()), "name": "Lightning Bolt",
            "set": "LEA", "collector_number": "161", "rarity": "common", "cmc": 1,
            "type_line": "Instant", "colors": ["R"], "color_identity": ["R"],
            "prices": {"usd": "5.00"}}
    bear = {"id": str(uuid.uuid4()), "oracle_id": str(uuid.uuid4()), "name": "Grizzly Bears",
            "set": "LEA", "collector_number": "200", "rarity": "common", "cmc": 2,
            "type_line": "Creature — Bear", "colors": ["G"], "color_identity": ["G"],
            "prices": {"usd": "0.25"}}
    mountain = {"id": str(uuid.uuid4()), "oracle_id": str(uuid.uuid4()), "name": "Mountain",
                "set": "LEA", "collector_number": "295", "rarity": "common", "cmc": 0,
                "type_line": "Basic Land — Mountain", "colors": [], "color_identity": ["R"],
                "prices": {"usd": "0.10"}}
    for raw in (bolt, bear, mountain):
        session.add(Card(**card_to_columns(raw)))
    await session.commit()


@pytest.mark.asyncio
async def test_deck_stats_curve_colors_value(session):
    await _seed(session)
    deck = await create_deck(session, "Test", "4 Lightning Bolt\n4 Grizzly Bears\n12 Mountain")
    stats = await deck_stats(session, deck)
    # Curve excludes the 12 lands; spells: 4 at MV1, 4 at MV2.
    curve = {b.label: b.count for b in stats.mana_curve}
    assert curve == {"1": 4, "2": 4}
    colors = {b.label: b.count for b in stats.by_color}
    assert colors == {"Red": 4, "Green": 4}  # lands excluded from the color pie
    # total value counts every card: 4*5.00 + 4*0.25 + 12*0.10 = 22.20
    assert stats.total_value == 22.20


@pytest.mark.asyncio
async def test_collect_export_cards_annotates(session):
    await _seed(session)
    deck = await create_deck(session, "Test", "4 Lightning Bolt\n12 Mountain")
    cards = {c.name: c for c in await collect_export_cards(session, deck)}
    assert cards["Lightning Bolt"].set_code == "lea"
    assert cards["Lightning Bolt"].collector_number == "161"


@pytest.mark.asyncio
async def test_export_route(client, session):
    await _seed(session)
    deck = await create_deck(session, "My Burn Deck", "4 Lightning Bolt\n12 Mountain")
    resp = await client.get(f"/decks/{deck.id}/export?fmt=arena")
    assert resp.status_code == 200
    assert "4 Lightning Bolt (LEA) 161" in resp.text
    assert 'filename="my-burn-deck.txt"' in resp.headers["content-disposition"]

    dek = await client.get(f"/decks/{deck.id}/export?fmt=mtgo")
    assert dek.status_code == 200
    assert "<Deck" in dek.text
    assert 'filename="my-burn-deck.dek"' in dek.headers["content-disposition"]
