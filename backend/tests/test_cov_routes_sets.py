"""Coverage tests for src.routes.sets: index redirect, the set-release calendar, per-set page.

Handlers are called directly so coverage records the lines past each DB ``await``. The calendar's
Scryfall sync is pre-seeded with a fake client so the handler's own refresh is a cache no-op (no
network).
"""

import uuid

import pytest
from fastapi import HTTPException
from src.models import Card, CollectionCard
from src.routes import sets as R
from src.scryfall.mapping import card_to_columns
from src.set_calendar import refresh_sets
from starlette.requests import Request


def _request(path="/"):
    return Request({"type": "http", "http_version": "1.1", "method": "GET", "scheme": "http",
                    "path": path, "raw_path": path.encode(), "query_string": b"", "root_path": "",
                    "headers": [], "server": ("test", 80), "client": ("test", 80),
                    "app": R.router})


class _FakeClient:
    async def get_json(self, url):
        return {"data": [
            {"code": "fut", "name": "Future Set", "released_at": "2099-01-01",
             "set_type": "expansion", "card_count": 10, "digital": False},
        ]}


async def _seed_set(session):
    card = Card(**card_to_columns(
        {"id": str(uuid.uuid4()), "oracle_id": str(uuid.uuid4()), "name": "Aaa", "set": "tst",
         "set_name": "Test Set", "set_type": "expansion", "collector_number": "1",
         "rarity": "common"}
    ))
    session.add(card)
    await session.flush()
    session.add(CollectionCard(scryfall_id=card.scryfall_id, quantity=1))
    await session.commit()
    return card


@pytest.mark.asyncio
async def test_list_sets_redirect():
    resp = await R.list_sets()
    assert resp.status_code == 307
    assert resp.headers["location"] == "/collection?tab=stats&view=sets"


@pytest.mark.asyncio
async def test_calendar(session):
    # Pre-seed the calendar so the route's refresh is a cache no-op (no network call).
    await refresh_sets(session, force=True, client=_FakeClient())
    resp = await R.calendar(_request("/calendar"), session)
    assert resp.status_code == 200 and b"Future Set" in resp.body


@pytest.mark.asyncio
async def test_set_page_and_404(session):
    await _seed_set(session)
    page = await R.set_page("tst", _request("/sets/tst"), session)
    assert page.status_code == 200 and b"Test Set" in page.body

    with pytest.raises(HTTPException) as e:
        await R.set_page("zzz", _request("/sets/zzz"), session)
    assert e.value.status_code == 404
