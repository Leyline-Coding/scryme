"""Wishlist service: the cards the user wants to acquire.

One row per printing. Adding a printing that's already listed raises its quantity to the larger of
the two (so re-adding a deck's missing cards is idempotent rather than ever-growing). The list view
estimates a total cost from current Scryfall USD prices.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.currency import unit_price
from src.decks import deck_missing
from src.models import Card, Deck, WishlistItem


def _as_uuid(scryfall_id) -> uuid.UUID:
    return scryfall_id if isinstance(scryfall_id, uuid.UUID) else uuid.UUID(str(scryfall_id))


async def add_to_wishlist(
    session: AsyncSession, scryfall_id, quantity: int = 1, note: str | None = None
) -> WishlistItem | None:
    """Add (or bump) a printing on the wishlist. Returns None if the card is unknown."""
    sid = _as_uuid(scryfall_id)
    if await session.get(Card, sid) is None:
        return None
    quantity = max(1, quantity)
    item = (
        await session.execute(select(WishlistItem).where(WishlistItem.scryfall_id == sid))
    ).scalar_one_or_none()
    if item is None:
        item = WishlistItem(scryfall_id=sid, quantity=quantity, note=note)
        session.add(item)
    else:
        item.quantity = max(item.quantity, quantity)
        if note:
            item.note = note
    await session.commit()
    await session.refresh(item)
    return item


async def remove_from_wishlist(session: AsyncSession, scryfall_id) -> None:
    sid = _as_uuid(scryfall_id)
    await session.execute(delete(WishlistItem).where(WishlistItem.scryfall_id == sid))
    await session.commit()


async def is_wishlisted(session: AsyncSession, scryfall_id) -> bool:
    sid = _as_uuid(scryfall_id)
    return (
        await session.execute(
            select(WishlistItem.id).where(WishlistItem.scryfall_id == sid)
        )
    ).first() is not None


async def add_deck_missing(session: AsyncSession, deck: Deck) -> int:
    """Add every matched card the deck still needs to the wishlist. Returns the number added."""
    missing = await deck_missing(session, deck)
    for entry in missing:
        await add_to_wishlist(
            session, entry.scryfall_id, entry.missing, note=f"for {deck.name}"
        )
    return len(missing)


@dataclass
class WishlistView:
    items: list[WishlistItem]
    total_cost: float
    total_cards: int


async def list_wishlist(session: AsyncSession, currency: str = "usd") -> WishlistView:
    items = list(
        (
            await session.execute(
                select(WishlistItem).order_by(WishlistItem.added_at.desc())
            )
        )
        .scalars()
        .all()
    )
    total_cost = sum(
        item.quantity * unit_price(item.card.prices, "normal", currency) for item in items
    )
    total_cards = sum(item.quantity for item in items)
    return WishlistView(items=items, total_cost=round(total_cost, 2), total_cards=total_cards)
