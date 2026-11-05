"""Custom binders: CRUD and owned-card membership (#206).

Flat, user-named binders of owned cards. Membership is by printing (``scryfall_id``); a card can
only be added if it is in the collection.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.collection_edit import locate_stacks
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
    ''' When a binder is deleted, all the cards that point to that location are moved to the parent object (unfiled is the root). '''
    binder = await session.get(Binder, binder_id)
    collection_cards = await get_all_binder_cards(session, binder_id)
    if binder:
        await locate_stacks(session, [c.id for c in collection_cards], None)
        await session.delete(binder)
        await session.commit()


async def add_card(session: AsyncSession, binder_id: int, scryfall_id) -> bool:
    """Add an owned printing to a binder. False if unowned, missing binder, or already present."""
    sid = _as_uuid(scryfall_id)
    if await session.get(Binder, binder_id) is None:
        return False
    owned = await session.scalar(
        select(CollectionCard.id).where(CollectionCard.scryfall_id == sid).limit(1)
    )
    if owned is None:
        return False
    exists = await session.scalar(
        select(BinderCard.id).where(
            BinderCard.binder_id == binder_id, BinderCard.scryfall_id == sid
        )
    )
    if exists:
        return False
    session.add(BinderCard(binder_id=binder_id, scryfall_id=sid))
    await session.commit()
    return True


async def bulk_add_to_binder(session: AsyncSession, binder_id: int, scryfall_ids) -> int:
    """Add many owned printings to a binder; returns how many were newly added."""
    if await session.get(Binder, binder_id) is None:
        return 0
    sids = {_as_uuid(s) for s in scryfall_ids}
    if not sids:
        return 0
    owned = set((await session.execute(
        select(CollectionCard.scryfall_id).where(CollectionCard.scryfall_id.in_(sids))
    )).scalars().all())
    already = set((await session.execute(
        select(BinderCard.scryfall_id).where(
            BinderCard.binder_id == binder_id, BinderCard.scryfall_id.in_(sids)
        )
    )).scalars().all())
    added = 0
    for sid in sids:
        if sid in owned and sid not in already:
            session.add(BinderCard(binder_id=binder_id, scryfall_id=sid))
            added += 1
    if added:
        await session.commit()
    return added


async def get_all_binder_cards(session: AsyncSession, binder_id: int) -> list[CollectionCard]:
    return list((await session.execute(
        select(CollectionCard)
        .join(Location, CollectionCard.location == Location.id)
        .where(
            Location.type == "binder",
            Location.foreign_id == binder_id
        )
    )).scalars().all())


async def binders_for_card(session: AsyncSession, scryfall_id) -> set[int]:
    """Ids of binders that already contain this printing (to check/uncheck in the add UI)."""
    rows = (await session.execute(
        select(BinderCard.binder_id).where(BinderCard.scryfall_id == _as_uuid(scryfall_id))
    )).scalars().all()
    return set(rows)
