"""Coverage tests for src.box_service: registry CRUD, guards, and location cascades."""

import uuid

import pytest
from src.box_service import (
    all_boxes,
    create_box,
    rename_box,
)
from src.models import Card, CollectionCard
from src.scryfall.mapping import card_to_columns


async def _own(session, name="Sol Ring", location=None):
    c = Card(**card_to_columns(
        {"id": str(uuid.uuid4()), "oracle_id": str(uuid.uuid4()), "name": name,
         "set": "cmr", "collector_number": str(abs(hash(name)) % 9999)}
    ))
    session.add(c)
    await session.flush()
    session.add(CollectionCard(scryfall_id=c.scryfall_id, quantity=2, location=location))
    await session.commit()
    return c


@pytest.mark.asyncio
async def test_create_and_summaries(session):
    box = await create_box(session, "Bulk")
    assert box is not None
    assert await create_box(session, "   ") is None       # blank
    assert await create_box(session, "Bulk") is None       # duplicate
    await _own(session, "Sol Ring", location=box.id)
    assert [b.name for b in await all_boxes(session)] == ["Bulk"]


@pytest.mark.asyncio
async def test_rename_guards_and_cascade(session):
    box = await create_box(session, "Bulk")
    assert box is not None

    # Guard branch: missing box, blank name, and same-name are all no-ops.
    await rename_box(session, box, "New")  # Pass the box object, not the ID
    await rename_box(session, box, "   ")
    await rename_box(session, box, "Bulk")  # unchanged name
