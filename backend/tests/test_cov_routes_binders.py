"""Coverage tests for src.routes.binders.

Handlers are called directly (with a constructed Request) so coverage records the lines after each
DB ``await`` (a Starlette-dispatched async handler loses the trace across SQLAlchemy's greenlet
hop). Behaviour is still asserted on every call.
"""

import uuid

import pytest
from fastapi import HTTPException
from src.binder_service import binders_for_card, create_binder
from src.config import get_settings
from src.models import Card, CollectionCard
from src.routes import binders as R
from src.scryfall.mapping import card_to_columns
from starlette.requests import Request


def _request(path="/"):
    return Request({"type": "http", "http_version": "1.1", "method": "GET", "scheme": "http",
                    "path": path, "raw_path": path.encode(), "query_string": b"", "root_path": "",
                    "headers": [], "server": ("test", 80), "client": ("test", 80),
                    "app": R.router})


async def _own(session, name="Sol Ring", owned=True, binder_name=None):
    c = Card(**card_to_columns(
        {"id": str(uuid.uuid4()), "oracle_id": str(uuid.uuid4()), "name": name,
         "set": "cmr", "collector_number": str(abs(hash(name)) % 9999)}
    ))
    session.add(c)
    await session.flush()
    if owned:
        session.add(CollectionCard(scryfall_id=c.scryfall_id, quantity=1, binder_name=binder_name))
    await session.commit()
    return c


@pytest.mark.asyncio
async def test_binders_home_redirect():
    resp = await R.binders_home()
    assert resp.status_code == 307 and resp.headers["location"] == "/collection?tab=binders"


@pytest.mark.asyncio
async def test_binder_view_and_404(session):
    b = await create_binder(session, "Ramp")
    owned = await _own(session, "Swords to Plowshares")
    from src.binder_service import add_card
    await add_card(session, b.id, str(owned.scryfall_id))

    view = await R.binder_view(_request(f"/binders/view/{b.id}"), b.id, session)
    assert view.status_code == 200 and b"Swords to Plowshares" in view.body

    with pytest.raises(HTTPException) as e:
        await R.binder_view(_request("/binders/view/0"), 999999, session)
    assert e.value.status_code == 404


@pytest.mark.asyncio
async def test_new_rename_delete_binder(session):
    new = await R.new_binder(name="Cats", session=session)
    assert new.status_code == 303 and new.headers["location"] == "/collection?tab=binders"
    from src.binder_service import binder_summaries
    b = (await binder_summaries(session))[0]

    ren = await R.rename_binder_route(b.id, name="Kittens", session=session)
    assert ren.status_code == 303
    assert (await binder_summaries(session))[0].name == "Kittens"

    dele = await R.delete_binder_route(b.id, session=session)
    assert dele.status_code == 303 and dele.headers["location"] == "/collection?tab=binders"
    assert await binder_summaries(session) == []


@pytest.mark.asyncio
async def test_card_binder_add_remove_partials(session):
    b = await create_binder(session, "Ramp")
    owned = await _own(session, "Sol Ring")
    sid = str(owned.scryfall_id)

    add = await R.card_binder_add(_request("/card"), sid, binder_id=str(b.id), session=session)
    assert add.status_code == 200 and b"Ramp" in add.body
    assert await binders_for_card(session, sid) == {b.id}

    # Blank binder id -> no membership change, still renders the partial.
    noop = await R.card_binder_add(_request("/card"), sid, binder_id="  ", session=session)
    assert noop.status_code == 200

    rm = await R.card_binder_remove(_request("/card"), sid, binder_id=str(b.id), session=session)
    assert rm.status_code == 200
    assert await binders_for_card(session, sid) == set()
    # Blank binder id on remove -> no-op partial.
    noop_rm = await R.card_binder_remove(_request("/card"), sid, binder_id="", session=session)
    assert noop_rm.status_code == 200


@pytest.mark.asyncio
async def test_remove_card_route(session):
    b = await create_binder(session, "Ramp")
    owned = await _own(session, "Sol Ring")
    from src.binder_service import add_card
    await add_card(session, b.id, str(owned.scryfall_id))
    resp = await R.remove_card_route(b.id, scryfall_id=str(owned.scryfall_id), session=session)
    assert resp.status_code == 303
    assert await binders_for_card(session, str(owned.scryfall_id)) == set()


@pytest.mark.asyncio
async def test_binder_cards_browse_named_and_unsorted(session):
    await _own(session, "Alpha", binder_name="Reds")
    await _own(session, "Gamma", binder_name=None)

    named = await R.binder_cards_browse(_request("/binders/cards"), name="Reds", session=session)
    assert b"Alpha" in named.body and b"Gamma" not in named.body

    unsorted = await R.binder_cards_browse(_request("/binders/cards"), name="__none__",
                                           session=session)
    assert b"Gamma" in unsorted.body and b"Alpha" not in unsorted.body


@pytest.mark.asyncio
async def test_binder_remove_from_collection_and_relocate(session):
    from sqlalchemy import func, select
    a = await _own(session, "Alpha", binder_name="Reds")   # 1 stack in Reds
    b = await _own(session, "Beta", binder_name="Reds")
    # "Remove from collection": delete Alpha's stacks filed in Reds.
    await R.binder_remove_from_collection(binder="Reds", scryfall_id=str(a.scryfall_id),
                                          session=session)
    assert await session.scalar(
        select(func.count()).select_from(CollectionCard)
        .where(CollectionCard.scryfall_id == a.scryfall_id)) == 0
    # "Change location": move Beta from Reds to Blues.
    await R.binder_relocate(binder="Reds", scryfall_id=str(b.scryfall_id), new_binder="Blues",
                            session=session)
    moved = await session.scalar(
        select(CollectionCard.binder_name).where(CollectionCard.scryfall_id == b.scryfall_id))
    assert moved == "Blues"


@pytest.mark.asyncio
async def test_binder_browse_offers_other_binders(session):
    await _own(session, "Alpha", binder_name="Reds")
    await _own(session, "Gamma", binder_name="Blues")
    page = await R.binder_cards_browse(_request("/binders/cards"), name="Reds", session=session)
    assert b"Blues" in page.body  # the relocate datalist lists other binders


@pytest.mark.asyncio
async def test_readonly_guards(session, monkeypatch):
    monkeypatch.setattr(get_settings(), "read_only", True)
    for coro in (
        R.new_binder(name="x", session=session),
        R.rename_binder_route(1, name="x", session=session),
        R.delete_binder_route(1, session=session),
        R.remove_card_route(1, scryfall_id=str(uuid.uuid4()), session=session),
        R.card_binder_add(_request("/c"), str(uuid.uuid4()), binder_id="1", session=session),
        R.card_binder_remove(_request("/c"), str(uuid.uuid4()), binder_id="1", session=session),
        R.binder_remove_from_collection(binder="x", scryfall_id=str(uuid.uuid4()),
                                        session=session),
        R.binder_relocate(binder="x", scryfall_id=str(uuid.uuid4()), new_binder="y",
                          session=session),
    ):
        with pytest.raises(HTTPException) as e:
            await coro
        assert e.value.status_code == 403
