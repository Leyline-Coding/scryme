"""Coverage tests for src.routes.binders.

Handlers are called directly (with a constructed Request) so coverage records the lines after each
DB ``await`` (a Starlette-dispatched async handler loses the trace across SQLAlchemy's greenlet
hop). Behaviour is still asserted on every call.
"""

import uuid

import pytest
from fastapi import HTTPException
from src.binder_service import create_binder
from src.collection_edit import move_stack_location
from src.config import get_settings
from src.models import Card, CollectionCard, Location
from src.routes import binders as R
from src.scryfall.mapping import card_to_columns
from starlette.requests import Request


def _request(path="/"):
    return Request({"type": "http", "http_version": "1.1", "method": "GET", "scheme": "http",
                    "path": path, "raw_path": path.encode(), "query_string": b"", "root_path": "",
                    "headers": [], "server": ("test", 80), "client": ("test", 80),
                    "app": R.router})


async def _own(session, name="Sol Ring", owned=True, source_location_name=None):
    c = Card(**card_to_columns(
        {"id": str(uuid.uuid4()), "oracle_id": str(uuid.uuid4()), "name": name,
         "set": "cmr", "collector_number": str(abs(hash(name)) % 9999)}
    ))
    session.add(c)
    await session.flush()
    if owned:
        cc = CollectionCard(
            scryfall_id=c.scryfall_id, quantity=1, source_location_name=source_location_name
        )
        session.add(cc)
    await session.commit()
    return c, cc


@pytest.mark.asyncio
async def test_binders_home_redirect():
    resp = await R.binders_home()
    assert resp.status_code == 307 and resp.headers["location"] == "/collection/binders"


@pytest.mark.asyncio
async def test_binder_view_and_404(session):
    b = await create_binder(session, "Ramp")
    c, cc = await _own(session, "Swords to Plowshares")
    assert b is not None

    loc = Location(type='binder', foreign_id=b.id)
    session.add(loc)
    await session.commit()
    await move_stack_location(session, cc.id, loc.id)

    view = await R.binder_view(_request(f"/binders/view/{b.id}"), b.id, session)
    assert view.status_code == 200 and b"Swords to Plowshares" in view.body

    with pytest.raises(HTTPException) as e:
        await R.binder_view(_request("/binders/view/0"), 999999, session)
    assert e.value.status_code == 404



@pytest.mark.asyncio
async def test_readonly_guards(session, monkeypatch):
    monkeypatch.setattr(get_settings(), "read_only", True)
    for coro in (
        R.new_binder(name="x", session=session),
        R.rename_binder_route(1, name="x", session=session),
        R.delete_binder_route(1, session=session),
    ):
        with pytest.raises(HTTPException) as e:
            await coro
        assert e.value.status_code == 403
