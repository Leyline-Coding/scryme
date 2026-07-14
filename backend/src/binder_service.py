"""Custom binders + binder groups: CRUD and owned-card membership (#206)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import Binder, BinderCard, BinderGroup, Card, CollectionCard


def _as_uuid(value) -> uuid.UUID:
    return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))


@dataclass
class BinderView:
    id: int
    name: str
    group_id: int | None
    count: int


@dataclass
class GroupView:
    id: int | None
    name: str
    binders: list[BinderView]


async def _counts(session: AsyncSession) -> dict[int, int]:
    rows = (await session.execute(
        select(BinderCard.binder_id, func.count()).group_by(BinderCard.binder_id)
    )).all()
    return {bid: int(n) for bid, n in rows}


async def grouped_binders(session: AsyncSession) -> list[GroupView]:
    """All binders organized by group (ungrouped binders come last under a null group)."""
    counts = await _counts(session)
    groups = (await session.execute(
        select(BinderGroup).order_by(BinderGroup.name)
    )).scalars().all()
    binders = (await session.execute(select(Binder).order_by(Binder.name))).scalars().all()

    by_group: dict[int | None, list[BinderView]] = {}
    for b in binders:
        by_group.setdefault(b.group_id, []).append(
            BinderView(b.id, b.name, b.group_id, counts.get(b.id, 0))
        )
    out = [GroupView(g.id, g.name, by_group.get(g.id, [])) for g in groups]
    if by_group.get(None):
        out.append(GroupView(None, "Ungrouped", by_group[None]))
    return out


async def all_binders(session: AsyncSession) -> list[Binder]:
    return list((await session.execute(select(Binder).order_by(Binder.name))).scalars().all())


async def create_binder(session: AsyncSession, name: str, group_id: int | None = None) -> Binder:
    binder = Binder(name=name.strip()[:128], group_id=group_id)
    session.add(binder)
    await session.commit()
    return binder


async def rename_binder(session: AsyncSession, binder_id: int, name: str) -> None:
    binder = await session.get(Binder, binder_id)
    if binder and name.strip():
        binder.name = name.strip()[:128]
        await session.commit()


async def set_binder_group(session: AsyncSession, binder_id: int, group_id: int | None) -> None:
    binder = await session.get(Binder, binder_id)
    if binder:
        binder.group_id = group_id
        await session.commit()


async def delete_binder(session: AsyncSession, binder_id: int) -> None:
    binder = await session.get(Binder, binder_id)
    if binder:
        await session.delete(binder)
        await session.commit()


async def create_group(session: AsyncSession, name: str) -> BinderGroup:
    group = BinderGroup(name=name.strip()[:128])
    session.add(group)
    await session.commit()
    return group


async def rename_group(session: AsyncSession, group_id: int, name: str) -> None:
    group = await session.get(BinderGroup, group_id)
    if group and name.strip():
        group.name = name.strip()[:128]
        await session.commit()


async def delete_group(session: AsyncSession, group_id: int) -> None:
    """Delete a group; its binders survive (become ungrouped via ON DELETE SET NULL)."""
    group = await session.get(BinderGroup, group_id)
    if group:
        await session.delete(group)
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
