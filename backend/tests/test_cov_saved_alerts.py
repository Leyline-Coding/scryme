"""Coverage for src/saved_alerts.py: bad-query resilience, too-broad skip, and own-session path."""

import uuid

import pytest
from src import saved_alerts
from src.models import Card, SavedSearch
from src.saved_alerts import evaluate_alerts


def _card(name: str) -> Card:
    return Card(scryfall_id=uuid.uuid4(), name=name, set_code="tst",
                collector_number="1", raw={"name": name})


@pytest.mark.asyncio
async def test_bad_query_is_skipped_not_fatal(session):
    session.add(_card("Black Lotus"))
    # An unparsable query raises SearchError inside _match_ids -> logged + skipped (lines 46-48).
    session.add(SavedSearch(name="broken", query="bogusfield:zzz", scope="all"))
    session.add(SavedSearch(name="ok", query="lotus", scope="all", seen_ids=[]))
    await session.commit()
    # The good search still evaluates despite the broken one.
    assert await evaluate_alerts(session) == 1


@pytest.mark.asyncio
async def test_too_broad_search_is_skipped(session, monkeypatch):
    monkeypatch.setattr(saved_alerts, "MAX_MATCHES_TRACKED", 0)  # any non-empty match set -> None
    session.add(_card("Black Lotus"))
    session.add(SavedSearch(name="lotuses", query="lotus", scope="all"))
    await session.commit()
    # _match_ids returns None (too broad) -> line 50 continue, nothing recorded.
    assert await evaluate_alerts(session) == 0


@pytest.mark.asyncio
async def test_evaluate_alerts_opens_own_session(session):
    session.add(_card("Goblin Guide"))
    session.add(SavedSearch(name="goblins", query="goblin", scope="all"))
    await session.commit()
    # Called with no session -> opens (and closes, line 63) its own SessionLocal.
    assert await evaluate_alerts() == 0  # first run only baselines
