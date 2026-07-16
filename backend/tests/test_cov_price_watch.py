"""Coverage tests for src/price_watch.py: helper edge cases and the own-session path."""

import uuid

import pytest
from src.models import Card
from src.price_watch import (
    _condition_met,
    _usd,
    add_target,
    evaluate_targets,
    list_targets,
    remove_target,
    target_for,
    triggered_targets,
)


async def _card(session, usd):
    card = Card(scryfall_id=uuid.uuid4(), name="Watched", set_code="tst",
                collector_number="1", prices={"usd": usd}, raw={"name": "Watched"})
    session.add(card)
    await session.commit()
    return card


def test_usd_handles_bad_values():
    assert _usd(None) == 0.0
    assert _usd({}) == 0.0
    assert _usd({"usd": "not-a-number"}) == 0.0   # ValueError -> 0.0
    assert _usd({"usd": "2.50"}) == 2.5


def test_condition_met():
    assert _condition_met("below", 0.0, 5.0) is False   # zero/unknown price never fires
    assert _condition_met("below", 3.0, 5.0) is True
    assert _condition_met("below", 6.0, 5.0) is False
    assert _condition_met("above", 6.0, 5.0) is True
    assert _condition_met("above", 4.0, 5.0) is False


@pytest.mark.asyncio
async def test_add_target_validation(session):
    card = await _card(session, "3.00")
    sid = str(card.scryfall_id)
    assert await add_target(session, sid, "sideways", 5.0) is None    # bad direction
    assert await add_target(session, sid, "below", 0.0) is None       # non-positive threshold
    assert await add_target(session, "not-a-uuid", "below", 5.0) is None   # bad uuid
    assert await add_target(session, str(uuid.uuid4()), "below", 5.0) is None  # unknown card


@pytest.mark.asyncio
async def test_remove_target(session):
    card = await _card(session, "3.00")
    t = await add_target(session, str(card.scryfall_id), "below", 5.0)
    await remove_target(session, t.id)
    assert await list_targets(session) == []


@pytest.mark.asyncio
async def test_target_for_bad_uuid_and_lookup(session):
    assert await target_for(session, "not-a-uuid") is None
    card = await _card(session, "3.00")
    t = await add_target(session, str(card.scryfall_id), "below", 5.0)
    found = await target_for(session, str(card.scryfall_id))
    assert found is not None and found.id == t.id


@pytest.mark.asyncio
async def test_evaluate_targets_own_session(session):
    card = await _card(session, "2.00")
    await add_target(session, str(card.scryfall_id), "below", 5.0)
    # No session passed -> evaluate_targets opens and closes its own SessionLocal.
    newly = await evaluate_targets()
    assert newly == 1


@pytest.mark.asyncio
async def test_triggered_targets_lists_only_fired(session):
    hit = await _card(session, "2.00")
    miss = await _card(session, "20.00")
    await add_target(session, str(hit.scryfall_id), "below", 5.0)
    await add_target(session, str(miss.scryfall_id), "below", 5.0)
    await evaluate_targets(session)
    fired = await triggered_targets(session)
    assert [t.name for t in fired] == ["Watched"]
    assert all(t.triggered for t in fired)
    assert len(fired) == 1
