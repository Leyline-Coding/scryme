"""
"""

from __future__ import annotations

from collections.abc import Sequence
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import Card, CollectionCard, DeckCard, Location


async def find_swappable_deck_card_in_collection_card(session: AsyncSession, oracle_id: uuid.UUID | None) -> Sequence[CollectionCard]:
    if not oracle_id: return []
    return (await session.execute(
        select(CollectionCard)
        .join(Card, CollectionCard.scryfall_id==Card.scryfall_id)
        .outerjoin(Location, CollectionCard.location == Location.id)
        .where(Card.oracle_id == oracle_id, (Location.type != "deck") | (Location.id.is_(None)))
    )).scalars().all()


async def quickswap_deck_card(session: AsyncSession, dc: DeckCard) -> uuid.UUID | None:
    result = await find_swappable_deck_card_in_collection_card(session, dc.oracle_id)
    if(result and len(result) > 0):
        dc.finish = result[0].finish
        dc.language = result[0].language
        dc.scryfall_id = result[0].scryfall_id
        await session.commit()
        return result[0].scryfall_id
    else: 
        return None
