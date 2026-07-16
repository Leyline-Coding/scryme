"""Coverage for src/set_calendar.py — date parsing, default client, error + codeless branches."""

import datetime

import pytest
import src.set_calendar as sc
from src.scryfall.client import ScryfallError
from src.set_calendar import _parse_date, refresh_sets


def test_parse_date():
    assert _parse_date("2020-01-01") == datetime.date(2020, 1, 1)
    assert _parse_date(None) is None
    assert _parse_date("not-a-date") is None  # ValueError -> None


class _FakeCtxClient:
    """Stands in for `async with ScryfallClient() as c`."""

    def __init__(self, payload):
        self.payload = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get_json(self, url):
        return self.payload


class _ErrClient:
    async def get_json(self, url):
        raise ScryfallError("offline")


@pytest.mark.asyncio
async def test_refresh_uses_default_client_when_none_and_not_fresh(session, monkeypatch):
    payload = {"data": [
        {"code": "abc", "name": "Set A", "released_at": "2099-01-01", "digital": False},
        {"name": "No Code", "released_at": "2099-02-01"},  # missing code -> skipped
    ]}
    monkeypatch.setattr(sc, "ScryfallClient", lambda: _FakeCtxClient(payload))
    # Empty DB, not forced -> _is_fresh sees no rows (returns False) -> proceeds via default client.
    n = await refresh_sets(session)
    assert n == 1  # codeless set skipped


@pytest.mark.asyncio
async def test_refresh_returns_zero_on_client_error(session):
    assert await refresh_sets(session, force=True, client=_ErrClient()) == 0
