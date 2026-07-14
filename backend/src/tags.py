"""User-defined card tags, stored per owned stack on ``collection_card.tags``.

A tag is treated as a property of a *printing* you own: adding or removing one applies to every
stack of that ``scryfall_id`` (across finishes/binders), and the card's tag set is the union of
its stacks' tags. Tags are normalized (trimmed, lower-cased, length-capped) so ``tag:`` search and
the chips line up regardless of how they were typed.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import CollectionCard

MAX_TAG_LEN = 64


def normalize_tag(raw: str | None) -> str | None:
    """Trim, collapse internal whitespace, lower-case, and length-cap; None if empty."""
    collapsed = " ".join((raw or "").split()).lower()
    return collapsed[:MAX_TAG_LEN] or None


@dataclass
class TagSummary:
    name: str
    quantity: int   # sum of stack quantities carrying this tag
    stacks: int     # number of stacks carrying it


async def tag_summaries(session: AsyncSession) -> list[TagSummary]:
    """Every tag in use with its counts, for the Tags tab (each links to a ``tag:`` search)."""
    rows = (await session.execute(
        select(CollectionCard.tags, CollectionCard.quantity)
        .where(CollectionCard.tags.is_not(None))
    )).all()
    agg: dict[str, list[int]] = {}
    for tags, qty in rows:
        for tag in tags or []:
            entry = agg.setdefault(tag, [0, 0])
            entry[0] += qty or 0
            entry[1] += 1
    return [TagSummary(name=t, quantity=q, stacks=s) for t, (q, s) in sorted(agg.items())]


async def _stacks(session: AsyncSession, scryfall_id: uuid.UUID) -> list[CollectionCard]:
    return list(
        (
            await session.execute(
                select(CollectionCard).where(CollectionCard.scryfall_id == scryfall_id)
            )
        )
        .scalars()
        .all()
    )


async def card_tags(session: AsyncSession, scryfall_id: uuid.UUID) -> list[str]:
    """The sorted union of tags across every owned stack of this printing."""
    rows = await session.execute(
        select(CollectionCard.tags).where(CollectionCard.scryfall_id == scryfall_id)
    )
    out: set[str] = set()
    for (tags,) in rows.all():
        if tags:
            out.update(tags)
    return sorted(out)


async def add_card_tag(session: AsyncSession, scryfall_id: uuid.UUID, raw: str) -> list[str]:
    """Add a normalized tag to every owned stack of this printing. Returns the new tag set."""
    tag = normalize_tag(raw)
    if tag:
        for stack in await _stacks(session, scryfall_id):
            current = list(stack.tags or [])
            if tag not in current:
                stack.tags = sorted(current + [tag])
        await session.commit()
    return await card_tags(session, scryfall_id)


async def remove_card_tag(session: AsyncSession, scryfall_id: uuid.UUID, raw: str) -> list[str]:
    """Remove a tag from every owned stack of this printing. Returns the new tag set."""
    tag = normalize_tag(raw)
    if tag:
        for stack in await _stacks(session, scryfall_id):
            if stack.tags and tag in stack.tags:
                stack.tags = [t for t in stack.tags if t != tag]
        await session.commit()
    return await card_tags(session, scryfall_id)
