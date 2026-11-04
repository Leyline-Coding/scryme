""""""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import Binder, Box, CollectionCard, Deck, Location
from sqlalchemy.ext.asyncio import AsyncSession



LOCATION_TYPES = {
    "binder": Binder,
    "box": Box,
    "deck": Deck,
}




async def find_or_create_locations(session: AsyncSession, binders: bool = True, boxes: bool = True, decks: bool = True) -> list:
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
        rows = (
            await session.execute(
                select(model.id, model.name)
            )
        ).all()

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
    return {loc: {"type": type, "foreign_id": foreign_id, "quantity": int(qty),"stacks": int(stacks)} for loc, type, foreign_id, qty, stacks in rows}


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
    return [LocationSummary(key, counts[key]["type"], counts[key]["foreign_id"], counts[key]["quantity"], counts[key]["stacks"]) for key in counts.keys()]


async def get_location_summaries_remapped(*args, **kwargs) -> dict:
    """Registry boxes with their card counts (empty boxes included)."""
    grouped_counts = defaultdict(dict)
    for item in (await get_location_summaries(*args, **kwargs)):
        grouped_counts[item.type][item.foreign_id] = item
    return grouped_counts