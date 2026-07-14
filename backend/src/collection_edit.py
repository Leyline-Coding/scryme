"""Manual collection editing: add/increment a stack, nudge quantities, delete, and bulk actions.

A "stack" is one ``collection_card`` row, keyed by (scryfall_id, finish, condition, language,
binder). Adding reuses the matching stack (incrementing) so the unique constraint is never
violated; a quantity that drops to zero deletes the row. Bulk actions operate on a set of
printings selected in the results grid.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import Card, CollectionCard
from src.tags import add_card_tag


def _as_uuid(value) -> uuid.UUID:
    return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))


def _clean(value: str | None) -> str | None:
    """Trim a free-text field; empty -> None (so it matches the NULLable stack key)."""
    v = (value or "").strip()
    return v or None


def _eq_or_null(col, value):
    return col.is_(None) if value is None else col == value


async def add_or_increment(
    session: AsyncSession,
    scryfall_id,
    quantity: int = 1,
    *,
    finish: str = "normal",
    condition: str | None = None,
    language: str = "en",
    binder: str | None = None,
    purchase_price: float | None = None,
) -> CollectionCard | None:
    """Add a printing to the collection, incrementing the matching stack if it exists.

    Returns the stack, or None if the printing is unknown.
    """
    sid = _as_uuid(scryfall_id)
    if await session.get(Card, sid) is None:
        return None
    quantity = max(1, quantity)
    finish = finish if finish in ("normal", "foil", "etched") else "normal"
    condition, binder = _clean(condition), _clean(binder)
    language = (language or "en").strip().lower() or "en"

    stack = (
        await session.execute(
            select(CollectionCard).where(
                CollectionCard.scryfall_id == sid,
                CollectionCard.finish == finish,
                CollectionCard.language == language,
                _eq_or_null(CollectionCard.condition, condition),
                _eq_or_null(CollectionCard.binder_name, binder),
            )
        )
    ).scalar_one_or_none()

    if stack is None:
        stack = CollectionCard(
            scryfall_id=sid, quantity=quantity, finish=finish, condition=condition,
            language=language, binder_name=binder, purchase_price=purchase_price,
            source_format="manual",
        )
        session.add(stack)
    else:
        stack.quantity += quantity
    await session.commit()
    await session.refresh(stack)
    return stack


async def adjust_quantity(session: AsyncSession, stack_id: int, delta: int):
    """Change a stack's quantity by ``delta``; delete it if it reaches zero. Returns scryfall_id."""
    stack = await session.get(CollectionCard, stack_id)
    if stack is None:
        return None
    sid = stack.scryfall_id
    stack.quantity += delta
    if stack.quantity <= 0:
        await session.delete(stack)
    await session.commit()
    return sid


_UNSET = object()


async def update_stack(
    session: AsyncSession,
    stack_id: int,
    *,
    quantity: int | None = None,
    finish: str | None = None,
    condition=_UNSET,
    language: str | None = None,
    binder=_UNSET,
    tags=_UNSET,
):
    """Update fields on a stack (any left unset stay put). Returns the stack, or None if missing.

    ``quantity`` is clamped to >= 1 (use :func:`delete_stack` to remove a stack). ``condition`` /
    ``binder`` / ``tags`` accept an explicit ``None`` to clear them, so they use a sentinel default.
    """
    stack = await session.get(CollectionCard, stack_id)
    if stack is None:
        return None
    if quantity is not None:
        stack.quantity = max(1, quantity)
    if finish is not None:
        stack.finish = finish
    if language is not None:
        stack.language = language or "en"
    if condition is not _UNSET:
        stack.condition = _clean(condition)
    if binder is not _UNSET:
        stack.binder_name = _clean(binder)
    if tags is not _UNSET:
        stack.tags = tags or None
    await session.commit()
    return stack


async def delete_stack(session: AsyncSession, stack_id: int):
    """Delete a stack outright. Returns its scryfall_id (or None if it didn't exist)."""
    stack = await session.get(CollectionCard, stack_id)
    if stack is None:
        return None
    sid = stack.scryfall_id
    await session.delete(stack)
    await session.commit()
    return sid


async def bulk_add_to_collection(
    session: AsyncSession, scryfall_ids: list, quantity: int = 1
) -> int:
    """Add one default (normal/en) stack copy per printing. Returns how many were added."""
    added = 0
    for sid in scryfall_ids:
        if await add_or_increment(session, sid, quantity) is not None:
            added += 1
    return added


async def bulk_add_tag(session: AsyncSession, scryfall_ids: list, tag: str) -> int:
    """Add a tag to every owned stack of each printing. Returns how many printings were tagged."""
    tagged = 0
    for sid in scryfall_ids:
        result = await add_card_tag(session, _as_uuid(sid), tag)
        if result:
            tagged += 1
    return tagged


# --- duplicate stacks (#101) --------------------------------------------------------------------

@dataclass
class DuplicateGroup:
    scryfall_id: str
    name: str
    set_code: str
    collector_number: str
    finish: str
    condition: str | None
    language: str
    count: int              # how many stack rows
    total_quantity: int     # summed quantity across them
    binders: list[str]      # binder names involved


async def find_duplicate_stacks(session: AsyncSession) -> list[DuplicateGroup]:
    """Groups of stack rows for the same card (scryfall_id + finish + condition + language) that are
    split across more than one row — e.g. the same card recorded in different binders."""
    rows = (await session.execute(
        select(
            CollectionCard.scryfall_id, CollectionCard.finish, CollectionCard.condition,
            CollectionCard.language, func.count().label("n"),
            func.sum(CollectionCard.quantity).label("qty"),
            func.array_agg(
                func.coalesce(CollectionCard.binder_name, "(no binder)")
            ).label("binders"),
        )
        .group_by(CollectionCard.scryfall_id, CollectionCard.finish,
                  CollectionCard.condition, CollectionCard.language)
        .having(func.count() > 1)
    )).all()
    if not rows:
        return []
    sids = {r[0] for r in rows}
    cards = {
        c.scryfall_id: c for c in
        (await session.execute(select(Card).where(Card.scryfall_id.in_(sids)))).scalars().all()
    }
    groups = []
    for sid, finish, condition, language, n, qty, binders in rows:
        card = cards.get(sid)
        groups.append(DuplicateGroup(
            scryfall_id=str(sid), name=card.name if card else "Unknown card",
            set_code=(card.set_code if card else "") or "",
            collector_number=(card.collector_number if card else "") or "",
            finish=finish, condition=condition, language=language,
            count=int(n), total_quantity=int(qty or 0), binders=list(binders or []),
        ))
    groups.sort(key=lambda g: g.name.lower())
    return groups


async def merge_duplicate_group(
    session: AsyncSession, scryfall_id, finish: str, condition: str | None, language: str,
):
    """Merge all rows for one (card, finish, condition, language) into a single stack.

    The earliest row survives with the summed quantity and the union of tags; the rest are deleted.
    """
    rows = (await session.execute(
        select(CollectionCard).where(
            CollectionCard.scryfall_id == _as_uuid(scryfall_id),
            CollectionCard.finish == finish,
            _eq_or_null(CollectionCard.condition, condition),
            CollectionCard.language == language,
        ).order_by(CollectionCard.id)
    )).scalars().all()
    if len(rows) < 2:
        return rows[0] if rows else None
    survivor = rows[0]
    tags: list[str] = []
    for r in rows:
        for t in (r.tags or []):
            if t not in tags:
                tags.append(t)
    survivor.quantity = sum(r.quantity for r in rows)
    survivor.tags = tags or None
    for r in rows[1:]:
        await session.delete(r)
    await session.commit()
    return survivor


async def merge_all_duplicates(session: AsyncSession) -> int:
    """Merge every duplicate group. Returns how many groups were merged."""
    groups = await find_duplicate_stacks(session)
    for g in groups:
        await merge_duplicate_group(session, g.scryfall_id, g.finish, g.condition, g.language)
    return len(groups)
