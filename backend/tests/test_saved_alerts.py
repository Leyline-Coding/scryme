"""Saved-search alerts (#58): baseline, new-match detection, surfacing, and clearing."""

import uuid

import pytest
from src.models import Card, SavedSearch
from src.saved_alerts import (
    clear_new,
    evaluate_alerts,
    searches_with_new,
    total_new_matches,
)


def _card(name: str) -> Card:
    return Card(
        scryfall_id=uuid.uuid4(),
        name=name,
        set_code="tst",
        collector_number="1",
        raw={"name": name},
    )


async def _seed(session, names):
    for n in names:
        session.add(_card(n))
    await session.commit()


@pytest.mark.asyncio
async def test_alerts_baseline_then_new_match(session):
    await _seed(session, ["Black Lotus"])
    session.add(SavedSearch(name="lotuses", query="lotus", scope="all"))
    await session.commit()

    # First evaluation only records a baseline — no alert spam for existing matches.
    assert await evaluate_alerts(session) == 0
    assert await total_new_matches(session) == 0

    # A newly-ingested matching card shows up as a new match.
    await _seed(session, ["Lotus Petal"])
    assert await evaluate_alerts(session) == 1
    assert await total_new_matches(session) == 1

    with_new = await searches_with_new(session)
    assert len(with_new) == 1 and with_new[0]["count"] == 1

    # Re-evaluating without changes adds nothing.
    assert await evaluate_alerts(session) == 0
    assert await total_new_matches(session) == 1


@pytest.mark.asyncio
async def test_clear_new(session):
    await _seed(session, ["Goblin Guide"])
    saved = SavedSearch(name="goblins", query="goblin", scope="all", seen_ids=[])
    session.add(saved)
    await session.commit()

    assert await evaluate_alerts(session) == 1
    await clear_new(session, saved.id)
    assert await total_new_matches(session) == 0


@pytest.mark.asyncio
async def test_alerts_endpoint_and_open(client, session):
    await _seed(session, ["Goblin Guide"])
    saved = SavedSearch(name="goblins", query="goblin", scope="all", seen_ids=[])
    session.add(saved)
    await session.commit()
    await evaluate_alerts(session)

    resp = await client.get("/saved/alerts")
    assert resp.status_code == 200 and resp.json()["total"] == 1

    # Opening the saved search redirects to its run and clears the badge.
    resp = await client.get(f"/saved/{saved.id}/open", follow_redirects=False)
    assert resp.status_code == 303 and "/search?" in resp.headers["location"]
    resp = await client.get("/saved/alerts")
    assert resp.json()["total"] == 0
