"""Custom binders: CRUD and owned-card membership (#206).

Flat, user-named binders of owned cards. Membership is by printing (``scryfall_id``); a card can
only be added if it is in the collection.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.collection_edit import move_stacks_location
from src.models import Binder, CollectionCard, Location


def _as_uuid(value) -> uuid.UUID:
    return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))


async def all_binders(session: AsyncSession) -> list[Binder]:
    return list((await session.execute(select(Binder).order_by(Binder.name))).scalars().all())


async def create_binder(session: AsyncSession, name: str) -> Binder | None:
    """Create a binder; returns None if the name is blank or already taken."""
    name = name.strip()[:128]
    if not name:
        return None
    if await session.scalar(select(Binder.id).where(Binder.name == name)):
        return None
    binder = Binder(name=name)
    session.add(binder)
    await session.commit()
    return binder


async def rename_binder(session: AsyncSession, binder: Binder, name: str) -> str:
    if binder and name.strip():
        binder.name = name.strip()[:128]
        await session.commit()
    return binder.name


async def delete_binder(session: AsyncSession, binder_id: int) -> None:
    ''' When a binder is deleted, all the cards that point to that location are moved to the
    parent object (unfiled is the root). '''
    binder = await session.get(Binder, binder_id)
    collection_cards = await get_all_binder_cards(session, binder_id)
    if binder:
        await move_stacks_location(session, [c.id for c in collection_cards], None)
        await session.delete(binder)
        await session.commit()


async def get_all_binder_cards(session: AsyncSession, binder_id: int) -> list[CollectionCard]:
    return list((await session.execute(
        select(CollectionCard)
        .join(Location, CollectionCard.location == Location.id)
        .where(
            Location.type == "binder",
            Location.foreign_id == binder_id
        )
    )).scalars().all())
