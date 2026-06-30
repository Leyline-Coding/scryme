"""Undo last import (#59): snapshot, restore, prune, and the route."""

import uuid

import pytest
from sqlalchemy import func, select
from src.import_undo import (
    KEEP_SNAPSHOTS,
    latest_snapshot,
    snapshot_collection,
    snapshot_count,
    undo_last,
)
from src.models import Card, CollectionCard, ImportSnapshot


async def _card(session) -> Card:
    card = Card(
        scryfall_id=uuid.uuid4(), name="Undo Card", set_code="tst", collector_number="1",
        raw={"name": "Undo Card"},
    )
    session.add(card)
    await session.flush()
    return card


async def _own(session, card, qty=1, **kw):
    cc = CollectionCard(scryfall_id=card.scryfall_id, quantity=qty, **kw)
    session.add(cc)
    await session.flush()
    return cc


@pytest.mark.asyncio
async def test_snapshot_and_undo_restores_prior_state(session):
    card = await _card(session)
    await _own(session, card, qty=2, purchase_price=1.50, binder_name="Box A")
    await session.commit()

    await snapshot_collection(session, "manabox import")
    await session.commit()

    # Simulate a destructive merge: blow away the collection and add something else.
    await session.execute(CollectionCard.__table__.delete())
    other = await _card(session)
    await _own(session, other, qty=99)
    await session.commit()

    restored = await undo_last(session)
    assert restored is not None

    rows = list((await session.execute(select(CollectionCard))).scalars())
    assert len(rows) == 1
    assert rows[0].scryfall_id == card.scryfall_id
    assert rows[0].quantity == 2
    assert rows[0].purchase_price == 1.50
    assert rows[0].binder_name == "Box A"
    # The snapshot is consumed by the undo.
    assert await snapshot_count(session) == 0


@pytest.mark.asyncio
async def test_prune_keeps_recent(session):
    card = await _card(session)
    await _own(session, card)
    await session.commit()
    for _ in range(KEEP_SNAPSHOTS + 3):
        await snapshot_collection(session, "import")
        await session.commit()
    assert await session.scalar(select(func.count()).select_from(ImportSnapshot)) == KEEP_SNAPSHOTS


@pytest.mark.asyncio
async def test_undo_route(client, session):
    card = await _card(session)
    await _own(session, card, qty=3)
    await session.commit()
    await snapshot_collection(session, "import")
    await session.commit()
    await session.execute(CollectionCard.__table__.delete())
    await session.commit()

    resp = await client.post("/upload/undo", follow_redirects=False)
    assert resp.status_code == 303
    assert await session.scalar(select(func.count()).select_from(CollectionCard)) == 1


@pytest.mark.asyncio
async def test_undo_noop_when_empty(session):
    assert await undo_last(session) is None
    assert await latest_snapshot(session) is None
