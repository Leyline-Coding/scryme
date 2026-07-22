"""Mirror owned-deck card edits into the collection (#298).

When a deck is imported as fully or partially owned, its owned cards were added to the collection.
Later edits to those cards' **quantity** or **printing** should keep the collection in sync — this
module decides when a deck card syncs and applies the corresponding collection change.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from src.collection_edit import adjust_owned
from src.models import Deck, DeckCard

OWNED_DECK = {"full", "partial"}


def syncs(deck: Deck, dc: DeckCard) -> bool:
    """True when edits to this deck card should mirror into the collection."""
    return deck.ownership in OWNED_DECK and dc.owned and dc.scryfall_id is not None


async def sync_quantity(session: AsyncSession, deck: Deck, dc: DeckCard, delta: int) -> None:
    """Apply a deck-card quantity delta to the collection when the card is owned."""
    if delta and syncs(deck, dc):
        await adjust_owned(session, str(dc.scryfall_id), delta)


async def sync_printing(
    session: AsyncSession, deck: Deck, dc: DeckCard, old_sid, quantity: int
) -> None:
    """Move an owned deck card's copies to its new printing when the printing changes."""
    if old_sid and str(old_sid) != str(dc.scryfall_id) and syncs(deck, dc):
        await adjust_owned(session, str(old_sid), -quantity)
        await adjust_owned(session, str(dc.scryfall_id), quantity)
