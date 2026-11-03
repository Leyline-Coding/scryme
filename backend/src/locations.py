""""""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import Binder, Box, Deck, Location



async def find_or_create_locations(session: AsyncSession, binders: bool = True, boxes: bool = True, decks: bool = True) -> list:
    """"""
    locations = []

    for enabled, model, type_name in [
        (binders, Binder, "binder"),
        (boxes, Box, "box"),
        (decks, Deck, "deck"),
    ]:
        if not enabled:
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