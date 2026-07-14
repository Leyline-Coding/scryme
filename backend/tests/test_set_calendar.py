"""Set-release calendar sync + view (#178)."""

import datetime

import pytest
from sqlalchemy import select
from src.models import SetRelease
from src.set_calendar import refresh_sets, set_calendar


class FakeClient:
    def __init__(self, payload):
        self.payload = payload
        self.calls = 0

    async def get_json(self, url):
        self.calls += 1
        return self.payload


_PAYLOAD = {
    "data": [
        {"code": "fut", "name": "Future Set", "released_at": "2099-01-01",
         "set_type": "expansion", "card_count": 300, "digital": False,
         "icon_svg_uri": "http://x/fut.svg"},
        {"code": "old", "name": "Old Set", "released_at": "2000-01-01",
         "set_type": "expansion", "card_count": 350, "digital": False},
        {"code": "dig", "name": "Digital Set", "released_at": "2098-01-01",
         "set_type": "alchemy", "card_count": 50, "digital": True},
        {"code": "und", "name": "Undated", "released_at": None, "digital": False},
    ]
}


@pytest.mark.asyncio
async def test_refresh_and_calendar(session):
    n = await refresh_sets(session, force=True, client=FakeClient(_PAYLOAD))
    assert n == 4
    stored = {s.code for s in (await session.execute(select(SetRelease))).scalars()}
    assert stored == {"fut", "old", "dig", "und"}

    cal = await set_calendar(session, today=datetime.date(2026, 7, 14))
    assert [s.code for s in cal.upcoming] == ["fut"]          # future, non-digital
    assert "old" in [s.code for s in cal.recent]              # past, non-digital
    assert "dig" not in [s.code for s in cal.upcoming]        # digital excluded
    assert "und" not in [s.code for s in cal.recent]          # undated excluded
    assert cal.synced_at is not None


@pytest.mark.asyncio
async def test_refresh_respects_cache(session):
    fake = FakeClient(_PAYLOAD)
    assert await refresh_sets(session, force=True, client=fake) == 4
    # A non-forced refresh right after is fresh → no second fetch.
    assert await refresh_sets(session, client=fake) == 0
    assert fake.calls == 1


@pytest.mark.asyncio
async def test_calendar_route(client, session):
    await refresh_sets(session, force=True, client=FakeClient(_PAYLOAD))
    resp = await client.get("/calendar")
    assert resp.status_code == 200
    assert "Future Set" in resp.text and "Set release calendar" in resp.text
