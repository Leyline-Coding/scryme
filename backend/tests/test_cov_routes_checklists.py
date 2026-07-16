"""Coverage tests for src.routes.checklists.

Handlers are called directly so coverage records the lines past each DB ``await``.
"""

import uuid

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select
from src.checklists import create_checklist
from src.config import get_settings
from src.models import Card, Checklist
from src.routes import checklists as R
from src.scryfall.mapping import card_to_columns
from starlette.requests import Request


def _request(path="/"):
    return Request({"type": "http", "http_version": "1.1", "method": "GET", "scheme": "http",
                    "path": path, "raw_path": path.encode(), "query_string": b"", "root_path": "",
                    "headers": [], "server": ("test", 80), "client": ("test", 80),
                    "app": R.router})


async def _card(session, name):
    c = Card(**card_to_columns(
        {"id": str(uuid.uuid4()), "oracle_id": str(uuid.uuid4()), "name": name, "set": "tst",
         "collector_number": "1", "prices": {"usd": "1.00"}}
    ))
    session.add(c)
    await session.commit()
    return c


@pytest.mark.asyncio
async def test_list_redirect():
    resp = await R.list_checklists()
    assert resp.status_code == 307 and resp.headers["location"] == "/collection?tab=checklists"


@pytest.mark.asyncio
async def test_create_view_delete(session):
    await _card(session, "Black Lotus")
    create = await R.create(name="P9", cards="Black Lotus\nMox Pearl", session=session)
    assert create.status_code == 303
    cid = int(create.headers["location"].split("/")[-1])

    view = await R.view_checklist(_request(f"/checklists/{cid}"), cid, session)
    assert view.status_code == 200 and b"Black Lotus" in view.body

    with pytest.raises(HTTPException) as e:
        await R.view_checklist(_request("/checklists/0"), 999999, session)
    assert e.value.status_code == 404

    dele = await R.delete_checklist(cid, session)
    assert dele.status_code == 303
    assert await session.get(Checklist, cid) is None
    # Deleting a missing checklist is a no-op redirect.
    assert (await R.delete_checklist(999999, session)).status_code == 303


@pytest.mark.asyncio
async def test_checklist_to_wishlist(session):
    await _card(session, "Black Lotus")
    cl = await create_checklist(session, "P9", "Black Lotus")
    resp = await R.checklist_to_wishlist(cl.id, session)
    assert resp.status_code == 303 and resp.headers["location"] == "/wishlist"
    with pytest.raises(HTTPException) as e:
        await R.checklist_to_wishlist(999999, session)
    assert e.value.status_code == 404


@pytest.mark.asyncio
async def test_readonly_guards(session, monkeypatch):
    monkeypatch.setattr(get_settings(), "read_only", True)
    for coro in (
        R.create(name="x", cards="Black Lotus", session=session),
        R.delete_checklist(1, session),
        R.checklist_to_wishlist(1, session),
    ):
        with pytest.raises(HTTPException) as e:
            await coro
        assert e.value.status_code == 403
    from src.models import WishlistItem
    assert await session.scalar(select(func.count()).select_from(WishlistItem)) == 0
