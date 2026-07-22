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
async def test_add_edit_remove_items(session):
    await _card(session, "Black Lotus")
    await _card(session, "Mox Sapphire")
    cl = await create_checklist(session, "P9", "Black Lotus")
    assert len(cl.items) == 1

    # Add cards (one per line); duplicates of existing items are skipped.
    await R.add_items(cl.id, cards="Mox Sapphire\nBlack Lotus\nMox Emerald", session=session)
    await session.refresh(cl)
    names = {i.name for i in cl.items}
    assert names == {"Black Lotus", "Mox Sapphire", "Mox Emerald"}
    lotus = next(i for i in cl.items if i.name == "Black Lotus")
    emerald = next(i for i in cl.items if i.name == "Mox Emerald")
    assert lotus.scryfall_id is not None       # resolved
    assert emerald.scryfall_id is None          # not in the card DB

    # Rename an item -> re-resolves (Mox Emerald -> Mox Sapphire, which exists).
    await R.edit_item(cl.id, emerald.id, name="Mox Sapphire", session=session)
    renamed = await session.get(type(emerald), emerald.id)
    assert renamed.name == "Mox Sapphire" and renamed.scryfall_id is not None

    # Remove an item.
    await R.delete_item(cl.id, lotus.id, session=session)
    await session.refresh(cl)
    assert "Black Lotus" not in {i.name for i in cl.items}
    # Adding only cards already present adds nothing; a blank/foreign rename is a no-op.
    before = len(cl.items)
    await R.add_items(cl.id, cards="Mox Sapphire", session=session)
    await session.refresh(cl)
    assert len(cl.items) == before
    await R.edit_item(cl.id, 999999, name="X", session=session)     # foreign item -> no-op
    await R.edit_item(cl.id, renamed.id, name="   ", session=session)  # blank name -> no-op
    # Missing checklist / foreign item are graceful.
    with pytest.raises(HTTPException):
        await R.add_items(999999, cards="X", session=session)
    assert (await R.delete_item(cl.id, 999999, session=session)).status_code == 303


@pytest.mark.asyncio
async def test_readonly_guards(session, monkeypatch):
    monkeypatch.setattr(get_settings(), "read_only", True)
    for coro in (
        R.create(name="x", cards="Black Lotus", session=session),
        R.delete_checklist(1, session),
        R.checklist_to_wishlist(1, session),
        R.add_items(1, cards="Black Lotus", session=session),
        R.edit_item(1, 1, name="X", session=session),
        R.delete_item(1, 1, session=session),
    ):
        with pytest.raises(HTTPException) as e:
            await coro
        assert e.value.status_code == 403
    from src.models import WishlistItem
    assert await session.scalar(select(func.count()).select_from(WishlistItem)) == 0
