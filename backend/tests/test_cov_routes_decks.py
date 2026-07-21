"""Coverage tests for src.routes.decks.

Handlers are invoked directly (with a constructed Request) rather than only over HTTP: SQLAlchemy's
async greenlet hop stops coverage from tracing the lines that follow a DB ``await`` inside a
Starlette-dispatched handler, so a direct ``await handler(...)`` from the test coroutine is what
records them. Every call still asserts the real response / side effect.
"""

import uuid

import pytest
from fastapi import HTTPException
from src.config import get_settings
from src.decks import create_deck
from src.models import Card, CollectionCard, Deck
from src.routes import decks as R
from src.scryfall.mapping import card_to_columns
from starlette.requests import Request


def _request(path="/", query=b""):
    return Request({"type": "http", "http_version": "1.1", "method": "GET", "scheme": "http",
                    "path": path, "raw_path": path.encode(), "query_string": query,
                    "root_path": "", "headers": [], "server": ("test", 80),
                    "client": ("test", 80), "app": R.router})  # app only needed for url_for


def _raw(name, *, oracle=None, set_code="tst", cn="1", usd="1.00", legal=None,
         type_line="Instant", ci=None):
    return {"id": str(uuid.uuid4()), "oracle_id": oracle or str(uuid.uuid4()), "name": name,
            "set": set_code, "collector_number": str(cn), "rarity": "common", "cmc": 1,
            "type_line": type_line, "colors": ci or ["R"], "color_identity": ci or ["R"],
            "released_at": "2020-01-01", "prices": {"usd": usd},
            "legalities": legal or {"commander": "legal", "modern": "legal"}}


async def _add(session, raw, owned=0):
    c = Card(**card_to_columns(raw))
    session.add(c)
    await session.flush()
    if owned:
        session.add(CollectionCard(scryfall_id=c.scryfall_id, quantity=owned))
    await session.commit()
    return c


# --- HTTP smoke tests (integration realism) -----------------------------------------------------

@pytest.mark.asyncio
async def test_list_decks_redirect(client):
    resp = await client.get("/decks", follow_redirects=False)
    assert resp.status_code == 307 and resp.headers["location"] == "/collection?tab=decks"


@pytest.mark.asyncio
async def test_new_deck_form_http(client):
    assert (await client.get("/decks/new")).status_code == 200


# --- direct-call tests (cover handler bodies past DB awaits) -------------------------------------

@pytest.mark.asyncio
async def test_new_deck_and_readonly(monkeypatch):
    resp = await R.new_deck(_request("/decks/new"))
    assert resp.status_code == 200
    monkeypatch.setattr(get_settings(), "read_only", True)
    with pytest.raises(HTTPException) as e:
        await R.new_deck(_request("/decks/new"))
    assert e.value.status_code == 403


@pytest.mark.asyncio
async def test_create_and_view(session):
    await _add(session, _raw("Lightning Bolt"), owned=1)
    redirect = await R.create(_request("/decks"), name="Burn", decklist="4 Lightning Bolt",
                              session=session)
    assert redirect.status_code == 303
    deck_id = int(redirect.headers["location"].split("/")[-1])

    view = await R.view_deck(_request(f"/decks/{deck_id}"), deck_id, "modern", session)
    assert view.status_code == 200

    with pytest.raises(HTTPException) as e:
        await R.view_deck(_request("/decks/0"), 999999, "", session)
    assert e.value.status_code == 404


@pytest.mark.asyncio
async def test_import_url_success_and_error(session, monkeypatch):
    await _add(session, _raw("Lightning Bolt"))

    async def ok(url, **kw):
        return "Imported", "1 Lightning Bolt"

    monkeypatch.setattr(R, "fetch_deck_from_url", ok)
    good = await R.import_url(_request("/decks/import-url"),
                              url="https://moxfield.com/decks/x", session=session)
    assert good.status_code == 303 and good.headers["location"].startswith("/decks/")

    from src.deck_import import DeckImportError

    async def boom(url, **kw):
        raise DeckImportError("private deck")

    monkeypatch.setattr(R, "fetch_deck_from_url", boom)
    bad = await R.import_url(_request("/decks/import-url"), url="bad", session=session)
    assert bad.status_code == 200 and "private deck" in bad.body.decode()


@pytest.mark.asyncio
async def test_build_form_and_preview(session):
    # An owned legendary creature -> build succeeds; an unknown name -> BuildError re-renders form.
    await _add(session, _raw("Solo Commander", type_line="Legendary Creature — Goblin"), owned=1)
    form = await R.build_form(_request("/decks/build"), session)
    assert form.status_code == 200

    ok = await R.build_preview(_request("/decks/build"), commander="Solo Commander",
                               session=session)
    assert ok.status_code == 200 and b"Solo Commander" in ok.body

    err = await R.build_preview(_request("/decks/build"), commander="No Such", session=session)
    assert err.status_code == 200 and b"No card named" in err.body


@pytest.mark.asyncio
async def test_edit_and_update_deck_card(session):
    oracle = str(uuid.uuid4())
    await _add(session, _raw("Boros Signet", oracle=oracle, set_code="cmm", cn="1"))
    b = await _add(session, _raw("Boros Signet", oracle=oracle, set_code="aart", cn="5"))
    deck = await create_deck(session, "C", "1 Boros Signet")
    dc = deck.cards[0]

    edit = await R.edit_deck_card(_request("/edit"), deck.id, dc.id, "commander", session)
    assert edit.status_code == 200

    upd = await R.update_deck_card(deck.id, dc.id, scryfall_id=str(b.scryfall_id), language="ja",
                                   proxy="on", special="on", format="commander", session=session)
    assert upd.status_code == 204
    assert upd.headers["hx-redirect"] == f"/decks/{deck.id}?format=commander"
    await session.refresh(dc)
    assert str(dc.scryfall_id) == str(b.scryfall_id) and dc.proxy and dc.special

    # update with no format -> bare redirect url.
    upd2 = await R.update_deck_card(deck.id, dc.id, language="en", session=session)
    assert upd2.headers["hx-redirect"] == f"/decks/{deck.id}"


@pytest.mark.asyncio
async def test_deck_card_404_paths(session):
    deck = await create_deck(session, "D", "1 UnmatchedName")
    dc = deck.cards[0]  # oracle_id is None
    # Missing card id.
    with pytest.raises(HTTPException) as e1:
        await R.edit_deck_card(_request("/edit"), deck.id, 999999, "", session)
    assert e1.value.status_code == 404
    # Unmatched line has no card to edit.
    with pytest.raises(HTTPException) as e2:
        await R.edit_deck_card(_request("/edit"), deck.id, dc.id, "", session)
    assert e2.value.status_code == 404


@pytest.mark.asyncio
async def test_export_deck(session):
    await _add(session, _raw("Lightning Bolt"))
    deck = await create_deck(session, "My Deck!", "4 Lightning Bolt")
    resp = await R.export_deck(deck.id, "text", session)
    assert resp.status_code == 200 and b"Lightning Bolt" in resp.body
    assert "my-deck.txt" in resp.headers["content-disposition"]
    # Unknown format falls back to text.
    assert (await R.export_deck(deck.id, "bogus", session)).status_code == 200
    with pytest.raises(HTTPException):
        await R.export_deck(999999, "text", session)


@pytest.mark.asyncio
async def test_delete_deck(session):
    await _add(session, _raw("Lightning Bolt"))
    deck = await create_deck(session, "D", "4 Lightning Bolt")
    redirect = await R.delete_deck(deck.id, session)
    assert redirect.status_code == 303 and redirect.headers["location"] == "/decks"
    assert await session.get(Deck, deck.id) is None
    # Deleting a missing deck is a no-op redirect.
    assert (await R.delete_deck(999999, session)).status_code == 303


@pytest.mark.asyncio
async def test_deck_to_wishlist(session):
    await _add(session, _raw("Lightning Bolt"), owned=0)
    deck = await create_deck(session, "Burn", "4 Lightning Bolt")
    resp = await R.deck_to_wishlist(deck.id, session)
    assert resp.status_code == 303 and resp.headers["location"] == "/wishlist"
    with pytest.raises(HTTPException):
        await R.deck_to_wishlist(999999, session)


@pytest.mark.asyncio
async def test_readonly_guards(session, monkeypatch):
    monkeypatch.setattr(get_settings(), "read_only", True)
    for coro in (
        R.create(_request("/decks"), name="x", decklist="1 Forest", session=session),
        R.import_url(_request("/i"), url="x", session=session),
        R.build_form(_request("/b"), session),
        R.build_preview(_request("/b"), commander="x", session=session),
        R.delete_deck(1, session),
        R.deck_to_wishlist(1, session),
        R.update_deck_card(1, 1, session=session),
        R.edit_deck_card(_request("/e"), 1, 1, "", session),
    ):
        with pytest.raises(HTTPException) as e:
            await coro
        assert e.value.status_code == 403
