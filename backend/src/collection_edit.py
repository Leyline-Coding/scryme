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
    location: str | None = None,
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
    condition, binder, location = _clean(condition), _clean(binder), _clean(location)
    language = (language or "en").strip().lower() or "en"

    stack = (
        await session.execute(
            select(CollectionCard).where(
                CollectionCard.scryfall_id == sid,
                CollectionCard.finish == finish,
                CollectionCard.language == language,
                _eq_or_null(CollectionCard.condition, condition),
                _eq_or_null(CollectionCard.binder_name, binder),
                _eq_or_null(CollectionCard.location, location),
            )
        )
    ).scalar_one_or_none()

    if stack is None:
        stack = CollectionCard(
            scryfall_id=sid, quantity=quantity, finish=finish, condition=condition,
            language=language, binder_name=binder, location=location,
            purchase_price=purchase_price, source_format="manual",
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
    location=_UNSET,
    tags=_UNSET,
):
    """Update fields on a stack (any left unset stay put). Returns the stack, or None if missing.

    ``quantity`` is clamped to >= 1 (use :func:`delete_stack` to remove a stack). ``condition`` /
    ``binder`` / ``location`` / ``tags`` take an explicit ``None`` to clear them (sentinel default).
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
    if location is not _UNSET:
        stack.location = _clean(location)
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


# --- organize by color identity (#160) ----------------------------------------------------------

_MONO = {"W": "White", "U": "Blue", "B": "Black", "R": "Red", "G": "Green"}
_GUILDS = {
    "WU": "Azorius", "WB": "Orzhov", "WR": "Boros", "WG": "Selesnya", "UB": "Dimir",
    "UR": "Izzet", "UG": "Simic", "BR": "Rakdos", "BG": "Golgari", "RG": "Gruul",
}
_SHARDS = {
    "WUB": "Esper", "WUR": "Jeskai", "WUG": "Bant", "WBR": "Mardu", "WBG": "Abzan",
    "WRG": "Naya", "UBR": "Grixis", "UBG": "Sultai", "URG": "Temur", "BRG": "Jund",
}


def color_identity_group(color_identity: list[str] | None) -> str:
    """Name of the color-identity binder a card belongs in (mono/guild/shard/…)."""
    key = "".join(c for c in "WUBRG" if c in set(color_identity or []))
    if not key:
        return "Colorless"
    if len(key) == 1:
        return _MONO[key]
    if len(key) == 2:
        return _GUILDS[key]
    if len(key) == 3:
        return _SHARDS[key]
    return "Four-color" if len(key) == 4 else "Five-color"


async def organize_by_color_identity(session: AsyncSession) -> int:
    """Set every owned stack's ``location`` to its card's color-identity group. Returns rows set."""
    from sqlalchemy import update

    rows = (await session.execute(
        select(CollectionCard.id, Card.color_identity)
        .join(Card, Card.scryfall_id == CollectionCard.scryfall_id)
    )).all()
    buckets: dict[str, list[int]] = {}
    for stack_id, ci in rows:
        buckets.setdefault(color_identity_group(ci), []).append(stack_id)
    for name, ids in buckets.items():
        await session.execute(
            update(CollectionCard).where(CollectionCard.id.in_(ids)).values(location=name)
        )
    await session.commit()
    return sum(len(ids) for ids in buckets.values())


@dataclass
class LocationSummary:
    location: str | None
    stacks: int
    quantity: int


async def location_summary(session: AsyncSession) -> list[LocationSummary]:
    """Card counts per physical location (None = unfiled), for the locations page."""
    rows = (await session.execute(
        select(CollectionCard.location, func.count(),
               func.sum(CollectionCard.quantity))
        .group_by(CollectionCard.location)
        .order_by(CollectionCard.location.nulls_last())
    )).all()
    return [LocationSummary(loc, int(n), int(q or 0)) for loc, n, q in rows]
