"""Custom binders: CRUD and owned-card membership (#206).

Flat, user-named binders of owned cards. Membership is by printing (``scryfall_id``); a card can
only be added if it is in the collection.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import Binder, BinderCard, Card, CollectionCard


def _as_uuid(value) -> uuid.UUID:
    return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))


@dataclass
class BinderSummary:
    id: int
    name: str
    count: int


async def _counts(session: AsyncSession) -> dict[int, int]:
    rows = (await session.execute(
        select(BinderCard.binder_id, func.count()).group_by(BinderCard.binder_id)
    )).all()
    return {bid: int(n) for bid, n in rows}


async def all_binders(session: AsyncSession) -> list[Binder]:
    return list((await session.execute(select(Binder).order_by(Binder.name))).scalars().all())


async def binder_summaries(session: AsyncSession) -> list[BinderSummary]:
    """Every binder with its card count, for the Binders tab."""
    counts = await _counts(session)
    binders = (await session.execute(select(Binder).order_by(Binder.name))).scalars().all()
    return [BinderSummary(b.id, b.name, counts.get(b.id, 0)) for b in binders]


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


async def rename_binder(session: AsyncSession, binder_id: int, name: str) -> None:
    binder = await session.get(Binder, binder_id)
    if binder and name.strip():
        binder.name = name.strip()[:128]
        await session.commit()


async def delete_binder(session: AsyncSession, binder_id: int) -> None:
    binder = await session.get(Binder, binder_id)
    if binder:
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


async def remove_card(session: AsyncSession, binder_id: int, scryfall_id) -> None:
    await session.execute(
        delete(BinderCard).where(
            BinderCard.binder_id == binder_id, BinderCard.scryfall_id == _as_uuid(scryfall_id)
        )
    )
    await session.commit()


async def binder_cards(session: AsyncSession, binder_id: int) -> list[Card]:
    return list((await session.execute(
        select(Card).join(BinderCard, BinderCard.scryfall_id == Card.scryfall_id)
        .where(BinderCard.binder_id == binder_id).order_by(Card.name)
    )).scalars().all())


async def binders_for_card(session: AsyncSession, scryfall_id) -> set[int]:
    """Ids of binders that already contain this printing (to check/uncheck in the add UI)."""
    rows = (await session.execute(
        select(BinderCard.binder_id).where(BinderCard.scryfall_id == _as_uuid(scryfall_id))
    )).scalars().all()
    return set(rows)
