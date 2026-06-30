"""Price watchlist (#88): thresholds, evaluation/reset, and the routes."""

import uuid

import pytest
from src.models import Card
from src.price_watch import (
    add_target,
    count_triggered,
    evaluate_targets,
    list_targets,
    remove_target,
)


async def _card(session, usd: str | None) -> Card:
    card = Card(
        scryfall_id=uuid.uuid4(),
        name="Watched Card",
        set_code="tst",
        collector_number="1",
        prices={"usd": usd} if usd is not None else {},
        raw={"name": "Watched Card"},
    )
    session.add(card)
    await session.commit()
    return card


@pytest.mark.asyncio
async def test_below_target_triggers_and_resets(session):
    card = await _card(session, "3.00")
    await add_target(session, str(card.scryfall_id), "below", 5.0)

    assert await evaluate_targets(session) == 1   # 3.00 <= 5.00 → fires
    assert await count_triggered(session) == 1

    # Re-evaluating an already-triggered target adds no new crossings.
    assert await evaluate_targets(session) == 0
    assert await count_triggered(session) == 1

    # Price rises back above the threshold → the alert resets.
    card.prices = {"usd": "9.00"}
    await session.commit()
    assert await evaluate_targets(session) == 0
    assert await count_triggered(session) == 0


@pytest.mark.asyncio
async def test_above_target(session):
    card = await _card(session, "12.00")
    await add_target(session, str(card.scryfall_id), "above", 10.0)
    assert await evaluate_targets(session) == 1
    rows = await list_targets(session)
    assert rows[0].triggered and rows[0].price == 12.0


@pytest.mark.asyncio
async def test_add_validation(session):
    card = await _card(session, "1.00")
    assert await add_target(session, str(card.scryfall_id), "sideways", 5.0) is None
    assert await add_target(session, str(uuid.uuid4()), "below", 5.0) is None  # unknown card


@pytest.mark.asyncio
async def test_remove(session):
    card = await _card(session, "1.00")
    t = await add_target(session, str(card.scryfall_id), "below", 5.0)
    await evaluate_targets(session)
    await remove_target(session, t.id)
    assert await list_targets(session) == []


@pytest.mark.asyncio
async def test_routes(client, session):
    card = await _card(session, "2.00")
    resp = await client.post(
        "/watch/add",
        data={"scryfall_id": str(card.scryfall_id), "direction": "below", "threshold": "5"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    await evaluate_targets(session)

    summary = (await client.get("/alerts")).json()
    assert summary["price"] == 1 and summary["total"] >= 1

    rows = await list_targets(session)
    resp = await client.post(f"/watch/{rows[0].id}/delete", follow_redirects=False)
    assert resp.status_code == 303
