"""Coverage tests for src.routes.wishlist: add/remove partials, HX vs plain remove, guards.

Handlers are invoked directly so coverage records the lines past each DB ``await``.
"""

import uuid

import pytest
from fastapi import HTTPException
from src.config import get_settings
from src.models import Card
from src.routes import wishlist as R
from src.scryfall.mapping import card_to_columns
from src.wishlist import is_wishlisted
from starlette.requests import Request


def _request(path="/", hx=False):
    headers = [(b"hx-request", b"true")] if hx else []
    return Request({"type": "http", "http_version": "1.1", "method": "POST", "scheme": "http",
                    "path": path, "raw_path": path.encode(), "query_string": b"", "root_path": "",
                    "headers": headers, "server": ("test", 80), "client": ("test", 80),
                    "app": R.router})


async def _card(session):
    c = Card(**card_to_columns(
        {"id": str(uuid.uuid4()), "oracle_id": str(uuid.uuid4()), "name": "Aaa",
         "set": "tst", "collector_number": "1", "prices": {"usd": "1.00"}}
    ))
    session.add(c)
    await session.commit()
    return c


def test_image_helper():
    # Uncached printing with a CDN image in raw -> returns the CDN url; empty raw -> "".
    with_img = Card(scryfall_id=uuid.uuid4(),
                    raw={"image_uris": {"normal": "https://cards/x.jpg"}})
    assert R._image(with_img) == "https://cards/x.jpg"
    assert R._image(Card(scryfall_id=uuid.uuid4(), raw={})) == ""


@pytest.mark.asyncio
async def test_wishlist_page_redirect():
    resp = await R.wishlist_page()
    assert resp.status_code == 307 and resp.headers["location"] == "/collection?tab=wishlist"


@pytest.mark.asyncio
async def test_add_and_remove_hx_and_plain(session):
    card = await _card(session)
    sid = str(card.scryfall_id)

    add = await R.add(_request(), scryfall_id=sid, quantity=1, note="", session=session)
    assert add.status_code == 200 and b"On wishlist" in add.body
    assert await is_wishlisted(session, sid)

    # HX remove -> renders the toggle button back in.
    hx = await R.remove(_request(hx=True), scryfall_id=sid, session=session)
    assert hx.status_code == 200 and b"Add to wishlist" in hx.body

    # Plain (non-HX) remove -> redirect to the wishlist tab.
    await R.add(_request(), scryfall_id=sid, quantity=1, note="n", session=session)
    plain = await R.remove(_request(hx=False), scryfall_id=sid, session=session)
    assert plain.status_code == 303 and plain.headers["location"] == "/wishlist"


@pytest.mark.asyncio
async def test_readonly_guards(session, monkeypatch):
    monkeypatch.setattr(get_settings(), "read_only", True)
    sid = str(uuid.uuid4())
    with pytest.raises(HTTPException) as e1:
        await R.add(_request(), scryfall_id=sid, quantity=1, note="", session=session)
    assert e1.value.status_code == 403
    with pytest.raises(HTTPException) as e2:
        await R.remove(_request(), scryfall_id=sid, session=session)
    assert e2.value.status_code == 403
