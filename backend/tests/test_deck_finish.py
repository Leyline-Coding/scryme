"""Deck finish persistence, foil-aware pricing, and re-sync of printings."""

import uuid

import pytest
from sqlalchemy import select
from src.config import get_settings
from src.decks import create_deck, deck_coverage, deck_stats, resync_printings
from src.models import Card, CollectionCard, Deck, DeckCard
from src.scryfall.mapping import card_to_columns

ORACLE = str(uuid.uuid4())


def _raw(sid, set_code, cn, usd, usd_foil):
    return {"id": sid, "oracle_id": ORACLE, "name": "Shiny Bolt", "set": set_code,
            "collector_number": cn, "type_line": "Instant", "cmc": 1, "color_identity": ["R"],
            "prices": {"usd": usd, "usd_foil": usd_foil},
            "legalities": {"commander": "legal"}}


async def _seed(session):
    """Two printings of one card; the 'plain' one is cheap, the 'fancy' one expensive."""
    plain = Card(**card_to_columns(_raw(str(uuid.uuid4()), "aaa", "1", "1.00", "2.00")))
    fancy = Card(**card_to_columns(_raw(str(uuid.uuid4()), "sld", "99", "10.00", "500.00")))
    session.add_all([plain, fancy])
    await session.commit()
    return plain, fancy


@pytest.mark.asyncio
async def test_finish_persisted_from_decklist(session):
    await _seed(session)
    deck = await create_deck(session, "D", "1 Shiny Bolt (SLD) 99 *F*\n1 Shiny Bolt (AAA) 1")
    by_finish = {c.finish: c for c in deck.cards}
    assert set(by_finish) == {"foil", "normal"}          # both lines kept, distinct finishes
    assert by_finish["foil"].scryfall_id is not None


@pytest.mark.asyncio
async def test_deck_value_uses_foil_price(session):
    _plain, fancy = await _seed(session)
    foil = await create_deck(session, "Foil", "1 Shiny Bolt (SLD) 99 *F*")
    plainer = await create_deck(session, "Plain", "1 Shiny Bolt (SLD) 99")
    # Same printing; the foil line is valued at usd_foil (500) not usd (10).
    assert (await deck_stats(session, foil)).total_value == 500.00
    assert (await deck_stats(session, plainer)).total_value == 10.00
    assert str(foil.cards[0].scryfall_id) == str(fancy.scryfall_id)


@pytest.mark.asyncio
async def test_missing_cost_uses_the_decks_finish(session):
    await _seed(session)
    deck = await create_deck(session, "Foil", "1 Shiny Bolt (SLD) 99 *F*")
    cov = await deck_coverage(session, deck)
    assert cov.missing_count == 1 and round(cov.missing_cost, 2) == 500.00


@pytest.mark.asyncio
async def test_resync_corrects_printing_and_finish(session):
    plain, fancy = await _seed(session)
    # A deck imported the old way: right card, wrong printing, no finish.
    deck = await create_deck(session, "Old", "1 Shiny Bolt (AAA) 1")
    assert str(deck.cards[0].scryfall_id) == str(plain.scryfall_id)

    changed = await resync_printings(session, deck, "1 Shiny Bolt (SLD) 99 *F*")
    assert changed == 1
    line = deck.cards[0]
    assert str(line.scryfall_id) == str(fancy.scryfall_id) and line.finish == "foil"
    # Re-running is a no-op once it already matches.
    assert await resync_printings(session, deck, "1 Shiny Bolt (SLD) 99 *F*") == 0


@pytest.mark.asyncio
async def test_resync_ignores_unknown_lines_and_keeps_quantity(session):
    await _seed(session)
    deck = await create_deck(session, "Old", "3 Shiny Bolt (AAA) 1")
    await resync_printings(session, deck, "1 Shiny Bolt (SLD) 99 *F*\n1 Not In Deck (SLD) 5")
    line = deck.cards[0]
    assert line.quantity == 3          # quantities are left alone
    assert line.finish == "foil"       # printing/finish corrected
    assert len(deck.cards) == 1        # no new lines added


@pytest.mark.asyncio
async def test_resync_leaves_unmatchable_lines_alone(session):
    """A line whose card isn't in the DB stays untouched rather than being blanked."""
    await _seed(session)
    deck = await create_deck(session, "Old", "1 Nonexistent Card")
    assert await resync_printings(session, deck, "1 Nonexistent Card (ZZZ) 7 *F*") == 0
    assert deck.cards[0].scryfall_id is None and deck.cards[0].finish == "normal"


# --- routes ------------------------------------------------------------------

@pytest.mark.asyncio
async def test_resync_route(client, session, monkeypatch):
    await _seed(session)
    deck = await create_deck(session, "Old", "1 Shiny Bolt (AAA) 1",
                             source_url="https://moxfield.com/decks/x")

    async def fake_fetch(url, **kw):
        return "Old", "1 Shiny Bolt (SLD) 99 *F*"

    monkeypatch.setattr("src.routes.decks.fetch_deck_from_url", fake_fetch)
    deck_id = deck.id
    resp = await client.post(f"/decks/{deck_id}/resync", follow_redirects=False)
    assert resp.status_code == 303
    # The route committed in its own session — read the column back directly.
    session.expire_all()
    finish = await session.scalar(select(DeckCard.finish).where(DeckCard.deck_id == deck_id))
    assert finish == "foil"


@pytest.mark.asyncio
async def test_resync_route_errors(client, session, monkeypatch):
    await _seed(session)
    # No source URL -> 400; missing deck -> 404.
    plain_deck = await create_deck(session, "Pasted", "1 Shiny Bolt (AAA) 1")
    assert (await client.post(f"/decks/{plain_deck.id}/resync")).status_code == 400
    assert (await client.post("/decks/99999/resync")).status_code == 404
    # A source that won't fetch surfaces as a bad-gateway rather than a crash.
    from src.deck_import import DeckImportError
    deck = await create_deck(session, "Src", "1 Shiny Bolt (AAA) 1",
                             source_url="https://moxfield.com/decks/x")

    async def boom(url, **kw):
        raise DeckImportError("nope")

    monkeypatch.setattr("src.routes.decks.fetch_deck_from_url", boom)
    assert (await client.post(f"/decks/{deck.id}/resync")).status_code == 502


@pytest.mark.asyncio
async def test_resync_links_a_source_url_first(client, session, monkeypatch):
    """A deck imported before source URLs were recorded can be linked to its source and repaired."""
    await _seed(session)
    deck = await create_deck(session, "Old", "1 Shiny Bolt (AAA) 1")
    assert deck.source_url is None

    async def fake_fetch(url, **kw):
        assert url == "https://moxfield.com/decks/y"
        return "Old", "1 Shiny Bolt (SLD) 99 *F*"

    monkeypatch.setattr("src.routes.decks.fetch_deck_from_url", fake_fetch)
    deck_id = deck.id
    resp = await client.post(f"/decks/{deck_id}/resync",
                             data={"url": "https://moxfield.com/decks/y"}, follow_redirects=False)
    assert resp.status_code == 303
    session.expire_all()
    got = await session.scalar(select(DeckCard.finish).where(DeckCard.deck_id == deck_id))
    assert got == "foil"
    # The URL sticks, so later re-syncs need no input.
    assert await session.scalar(select(Deck.source_url).where(Deck.id == deck_id)) == \
        "https://moxfield.com/decks/y"


@pytest.mark.asyncio
async def test_resync_read_only(client, session, monkeypatch):
    await _seed(session)
    deck = await create_deck(session, "Src", "1 Shiny Bolt (AAA) 1",
                             source_url="https://moxfield.com/decks/x")
    monkeypatch.setattr(get_settings(), "read_only", True)
    assert (await client.post(f"/decks/{deck.id}/resync")).status_code == 403


@pytest.mark.asyncio
async def test_full_import_adds_the_right_finish_to_collection(client, session):
    _plain, fancy = await _seed(session)
    resp = await client.post("/decks", data={
        "name": "Foil", "decklist": "1 Shiny Bolt (SLD) 99 *F*", "ownership": "full",
    }, follow_redirects=False)
    assert resp.status_code == 303
    stack = (await session.execute(
        select(CollectionCard).where(CollectionCard.scryfall_id == fancy.scryfall_id)
    )).scalars().first()
    assert stack is not None and stack.finish == "foil" and stack.quantity == 1
