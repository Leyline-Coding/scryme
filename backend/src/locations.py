""""""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import Binder, Box, CollectionCard, Deck, Location

LOCATION_TYPES = {
    "binder": Binder,
    "box": Box,
    "deck": Deck,
}


async def find_or_create_grouped_locations(*args, **kwargs):
    grouped_locations = defaultdict(list)
    for location in (await find_or_create_locations(*args, **kwargs)):
        grouped_locations[location.type].append(location)
    return grouped_locations


async def find_or_create_locations(
    session: AsyncSession, binders: bool = True, boxes: bool = True, decks: bool = True
) -> list:
    """"""
    locations = []

    for type_name, model in LOCATION_TYPES.items():
        if not {
            "binder": binders,
            "box": boxes,
            "deck": decks,
        }[type_name]:
            continue

        # Fetch source IDs and names
        rows = (await session.execute(select(model.id, model.name))).all()

        if not rows:
            continue

        source_names = {row.id: row.name for row in rows}
        ids = list(source_names.keys())

        # Fetch existing locations
        existing = (
            await session.scalars(
                select(Location).where(
                    Location.type == type_name,
                    Location.foreign_id.in_(ids),
                )
            )
        ).all()

        existing_ids = {loc.foreign_id for loc in existing}

        # Create missing locations
        new_locations = [
            Location(type=type_name, foreign_id=id)
            for id in set(ids) - existing_ids
        ]

        session.add_all(new_locations)

        # Combine existing and new
        for loc in existing + new_locations:
            loc.label = source_names[loc.foreign_id]

        locations.extend(existing + new_locations)

    await session.flush()
    await session.commit()
    return locations


async def find_location(session: AsyncSession, *args, **kwargs):
    return await session.scalar(_find_location(*args, **kwargs))


def _find_location(type: str, name: str):
    model = LOCATION_TYPES[type]

    return (
        select(Location.id)
        .join(model, Location.foreign_id == model.id)
        .where(
            Location.type == type,
            model.name.ilike(name)
        )
    )


async def _location_counts(session: AsyncSession) -> dict[int, dict]:
    rows = (await session.execute(
        select(
            CollectionCard.location,
            Location.type,
            Location.foreign_id,
            func.coalesce(func.sum(CollectionCard.quantity), 0),
            func.count())
        .join(Location, CollectionCard.location == Location.id)
        .where(CollectionCard.location.is_not(None))
        .group_by(CollectionCard.location, Location.type, Location.foreign_id)
    )).all()
    return {
        loc: {"type": type, "foreign_id": foreign_id, "quantity": int(qty),"stacks": int(stacks)}
        for loc, type, foreign_id, qty, stacks in rows
    }


@dataclass
class LocationSummary:
    id: int
    type: str
    foreign_id: int
    quantity: int
    stacks: int


async def get_location_summaries(session: AsyncSession) -> list[LocationSummary]:
    """Registry boxes with their card counts (empty boxes included)."""
    counts = await _location_counts(session)
    return [
        LocationSummary(
            key, counts[key]["type"], counts[key]["foreign_id"], counts[key]["quantity"],
            counts[key]["stacks"],
        )
        for key in counts.keys()
    ]


async def get_location_summaries_remapped(*args, **kwargs) -> dict:
    """Registry boxes with their card counts (empty boxes included)."""
    grouped_counts = defaultdict(dict)
    for item in (await get_location_summaries(*args, **kwargs)):
        grouped_counts[item.type][item.foreign_id] = item
    return grouped_counts




async def get_location_name(session, location):
    model = LOCATION_TYPES[location.type]

    return await session.scalar(
        select(model.name).where(model.id == location.foreign_id)
    )


async def find_or_create_location_by_name(session: AsyncSession, name: str | None) -> int | None:
    """The ``location.id`` of the place called *name*, creating a box when nothing has that name.

    Scanners, CSV-era data and old backups all speak in names ("Box A") while a stack points at a
    ``location`` row. A name matches an existing box, binder or deck case-insensitively, in that
    order. An unknown name becomes a new box — "I'm filing these somewhere I haven't registered
    yet" is the common case, and refusing over it would cost the person their work. Flushes but
    does not commit, so the caller owns the transaction.
    """
    name = (name or "").strip()
    if not name:
        return None
    found: tuple[str, int] | None = None
    for candidate, model in (("box", Box), ("binder", Binder), ("deck", Deck)):
        source_id = await session.scalar(
            select(model.id).where(func.lower(model.name) == name.lower()).order_by(model.id)
        )
        if source_id is not None:
            found = (candidate, source_id)
            break
    if found is None:
        box = Box(name=name[:128])
        session.add(box)
        await session.flush()
        found = ("box", box.id)
    type_name, source_id = found

    location_id = await session.scalar(
        select(Location.id)
        .where(Location.type == type_name, Location.foreign_id == source_id)
        .order_by(Location.id)
    )
    if location_id is None:
        location = Location(type=type_name, foreign_id=source_id)
        session.add(location)
        await session.flush()
        location_id = location.id
    return location_id
