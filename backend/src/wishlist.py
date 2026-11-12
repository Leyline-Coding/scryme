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
from src.pricing import effective_prices


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


async def adjust_wishlist_quantity(session: AsyncSession, scryfall_id, delta: int) -> None:
    """Adjust the quantity of a wishlist item by delta."""
    sid = _as_uuid(scryfall_id)
    item = (
        await session.execute(select(WishlistItem).where(WishlistItem.scryfall_id == sid))
    ).scalar_one_or_none()
    
    if item is not None:
        item.quantity += delta
        if item.quantity <= 0:
            # Remove the item if quantity becomes 0 or less
            await session.delete(item)
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
    card_prices: dict[int, float]
    total_cost: float
    total_cards: int


async def list_wishlist(
    session: AsyncSession, currency: str = "usd", source: str = "tcgplayer", sort_by: str = "name", sort_dir: str = "asc"
) -> WishlistView:
    
    # Apply sorting based on parameters
    if sort_by == "updated_at":
        order_by = WishlistItem.added_at
    elif sort_by == "quantity":
        order_by = WishlistItem.quantity
    elif sort_by == "price":
        order_by = Card.prices
    else:
        order_by = Card.name

    if sort_dir == "desc":
        order_by = order_by.desc()
    else:
        order_by = order_by.asc()
            
    items = list((await session.execute(
        select(WishlistItem).join(Card, Card.scryfall_id == WishlistItem.scryfall_id).order_by(order_by)
    )).scalars().all())

    total_cost = sum(
        item.quantity * unit_price(effective_prices(item.card, source), "normal", currency)
        for item in items
    )
    
    # Calculate individual item costs using the same logic as the total cost
    card_prices = {item.id:unit_price(effective_prices(item.card, source), "normal", currency) for item in items}
    
    total_cards = sum(item.quantity for item in items)
    return WishlistView(items=items, card_prices=card_prices, total_cost=round(total_cost, 2), total_cards=total_cards)
