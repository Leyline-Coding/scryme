"""Coverage tests for src.binder_service: CRUD, membership, and the missing-binder guards."""

import uuid

import pytest
from src.binder_service import (
    all_binders,
    create_binder,
    delete_binder,
    rename_binder,
)
from src.models import Card, CollectionCard
from src.scryfall.mapping import card_to_columns


async def _own(session, name="Sol Ring", owned=True):
    c = Card(**card_to_columns(
        {"id": str(uuid.uuid4()), "oracle_id": str(uuid.uuid4()), "name": name,
         "set": "cmr", "collector_number": str(abs(hash(name)) % 9999)}
    ))
    session.add(c)
    await session.flush()
    if owned:
        session.add(CollectionCard(scryfall_id=c.scryfall_id, quantity=1))
    await session.commit()
    return c


@pytest.mark.asyncio
async def test_create_rename_delete_and_summaries(session):
    assert await create_binder(session, "Ramp") is not None
    assert await create_binder(session, "  ") is None       # blank
    assert await create_binder(session, "Ramp") is None      # duplicate
