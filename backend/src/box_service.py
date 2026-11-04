"""Storage boxes (#160): a registry of physical boxes over ``collection_card.location``.

A box's "membership" is just the set of stacks whose ``location`` equals the box name, so ``loc:``
search and the location column keep working. This module manages the registry (create / rename /
delete, including empty boxes) and keeps the denormalized location strings in sync on rename/delete.
"""

from __future__ import annotations

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import Box, CollectionCard



async def all_boxes(session: AsyncSession) -> list[Box]:
    return list((await session.execute(select(Box).order_by(Box.name))).scalars().all())



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
