"""Storage boxes (#160): a registry of physical boxes over ``collection_card.location``.

A box's "membership" is just the set of stacks whose ``location`` equals the box name, so ``loc:``
search and the location column keep working. This module manages the registry (create / rename /
delete, including empty boxes) and keeps the denormalized location strings in sync on rename/delete.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import Box, CollectionCard


@dataclass
class BoxSummary:
    id: int | None      # None for ad-hoc locations not in the registry
    name: str
    quantity: int
    stacks: int


async def all_boxes(session: AsyncSession) -> list[Box]:
    return list((await session.execute(select(Box).order_by(Box.name))).scalars().all())


async def _location_counts(session: AsyncSession) -> dict[str, tuple[int, int]]:
    rows = (await session.execute(
        select(CollectionCard.location, func.coalesce(func.sum(CollectionCard.quantity), 0),
               func.count())
        .where(CollectionCard.location.is_not(None))
        .group_by(CollectionCard.location)
    )).all()
    return {loc: (int(qty), int(stacks)) for loc, qty, stacks in rows}


async def box_summaries(session: AsyncSession) -> list[BoxSummary]:
    """Registry boxes with their card counts (empty boxes included)."""
    counts = await _location_counts(session)
    boxes = await all_boxes(session)
    return [BoxSummary(b.id, b.name, *counts.get(b.name, (0, 0))) for b in boxes]


async def other_locations(session: AsyncSession) -> list[BoxSummary]:
    """Location strings present on stacks but not in the box registry (e.g. legacy auto-filed)."""
    counts = await _location_counts(session)
    names = {b.name for b in await all_boxes(session)}
    return [BoxSummary(None, loc, qty, stacks)
            for loc, (qty, stacks) in sorted(counts.items()) if loc not in names]


async def create_box(session: AsyncSession, name: str) -> Box | None:
    name = name.strip()[:128]
    if not name:
        return None
    if await session.scalar(select(Box.id).where(Box.name == name)):
        return None
    box = Box(name=name)
    session.add(box)
    await session.commit()
    return box


async def rename_box(session: AsyncSession, box_id: int, name: str) -> None:
    box = await session.get(Box, box_id)
    name = name.strip()[:128]
    if not box or not name or name == box.name:
        return
    old = box.name
    box.name = name
    await session.execute(
        update(CollectionCard).where(CollectionCard.location == old).values(location=name)
    )
    await session.commit()


async def delete_box(session: AsyncSession, box_id: int) -> None:
    """Delete the registry entry and unfile its stacks (clear their location)."""
    box = await session.get(Box, box_id)
    if not box:
        return
    await session.execute(
        update(CollectionCard).where(CollectionCard.location == box.name).values(location=None)
    )
    await session.delete(box)
    await session.commit()
