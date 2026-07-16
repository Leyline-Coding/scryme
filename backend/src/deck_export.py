"""Serialize a deck to the common decklist formats.

- ``text``     — plain ``qty Name`` lines, ``Sideboard`` separator. The most portable form.
- ``arena``    — ``qty Name (SET) NUM`` with a ``Sideboard`` section (MTG Arena import).
- ``moxfield`` — ``qty Name (SET) NUM`` with a ``SIDEBOARD:`` marker (Moxfield/Archidekt paste).
- ``mtgo``     — a ``.dek`` XML document (Magic Online).

The route builds the ``DeckExportCard`` list (joining each resolved printing to its set code +
collector number); these renderers are pure string functions so they're trivial to unit-test.
"""

from __future__ import annotations

from dataclasses import dataclass
from xml.sax.saxutils import quoteattr

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import Card, Deck

# fmt -> (file suffix, media type, UI label)
_TEXT_PLAIN = "text/plain"

EXPORT_FORMATS = {
    "text": ("txt", _TEXT_PLAIN, "Plain text"),
    "arena": ("txt", _TEXT_PLAIN, "Arena"),
    "moxfield": ("txt", _TEXT_PLAIN, "Moxfield"),
    "mtgo": ("dek", "application/xml", "MTGO (.dek)"),
}


@dataclass
class DeckExportCard:
    name: str
    quantity: int
    board: str  # main | side
    set_code: str | None
    collector_number: str | None


def _plain(c: DeckExportCard) -> str:
    return f"{c.quantity} {c.name}"


def _annotated(c: DeckExportCard) -> str:
    if c.set_code and c.collector_number:
        return f"{c.quantity} {c.name} ({c.set_code.upper()}) {c.collector_number}"
    return _plain(c)


def _split(cards: list[DeckExportCard]) -> tuple[list, list]:
    return ([c for c in cards if c.board != "side"], [c for c in cards if c.board == "side"])


def _list_format(cards: list[DeckExportCard], line, sideboard_header: str) -> str:
    main, side = _split(cards)
    lines = [line(c) for c in main]
    if side:
        lines += ["", sideboard_header, *[line(c) for c in side]]
    return "\n".join(lines) + "\n"


def _text(cards: list[DeckExportCard]) -> str:
    return _list_format(cards, _plain, "Sideboard")


def _arena(cards: list[DeckExportCard]) -> str:
    return _list_format(cards, _annotated, "Sideboard")


def _moxfield(cards: list[DeckExportCard]) -> str:
    return _list_format(cards, _annotated, "SIDEBOARD:")


def _mtgo(cards: list[DeckExportCard]) -> str:
    rows = [
        f'  <Cards CatID="0" Quantity="{c.quantity}" '
        f'Sideboard="{"true" if c.board == "side" else "false"}" Name={quoteattr(c.name)} />'
        for c in cards
    ]
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<Deck xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
        'xmlns:xsd="http://www.w3.org/2001/XMLSchema">\n'
        "  <NetDeckID>0</NetDeckID>\n"
        "  <PreconstructedDeckID>0</PreconstructedDeckID>\n"
        + "\n".join(rows)
        + ("\n" if rows else "")
        + "</Deck>\n"
    )


_RENDERERS = {"text": _text, "arena": _arena, "moxfield": _moxfield, "mtgo": _mtgo}


def render_deck(cards: list[DeckExportCard], fmt: str) -> str:
    return _RENDERERS.get(fmt, _text)(cards)


async def collect_export_cards(session: AsyncSession, deck: Deck) -> list[DeckExportCard]:
    """Build export rows for a deck, annotating each resolved card with set code + number."""
    sids = [c.scryfall_id for c in deck.cards if c.scryfall_id]
    meta: dict = {}
    if sids:
        rows = await session.execute(
            select(Card.scryfall_id, Card.set_code, Card.collector_number).where(
                Card.scryfall_id.in_(sids)
            )
        )
        meta = {sid: (sc, cn) for sid, sc, cn in rows.all()}
    out = []
    for c in deck.cards:
        sc, cn = meta.get(c.scryfall_id, (None, None))
        out.append(DeckExportCard(name=c.name, quantity=c.quantity, board=c.board,
                                  set_code=sc, collector_number=cn))
    return out
