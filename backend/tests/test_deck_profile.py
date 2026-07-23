"""Profile deck-listing + bulk import tests (#299)."""

import httpx
import pytest
from sqlalchemy import func, select
from src.config import get_settings
from src.deck_import import (
    DeckImportError,
    ProfileDeck,
    _profile_archidekt,
    _profile_moxfield,
    detect_profile,
    fetch_profile_decks,
)
from src.models import Deck

MOX_PAGE = {
    "totalResults": 2, "totalPages": 1,
    "data": [
        {"name": "Atraxa Superfriends", "format": "commander", "publicId": "abc123",
         "publicUrl": "https://moxfield.com/decks/abc123", "mainboardCount": 99,
         "visibility": "public"},
        {"name": "Secret Brew", "format": "modern", "publicId": "hid", "visibility": "private"},
        {"name": "No Url", "format": "modern", "visibility": "public"},   # unusable
    ],
}
ARCH_PAGE = {
    "count": 2, "next": None,
    "results": [
        {"id": 42, "name": "Elfball", "deckFormat": 3, "size": 100, "private": False},
        {"id": 43, "name": "Hidden", "deckFormat": 2, "size": 60, "private": True},
        {"name": "No Id", "deckFormat": 2, "size": 60, "private": False},  # unusable
    ],
}


# --- pure mappers ------------------------------------------------------------

def test_profile_moxfield_maps_and_skips_private():
    decks = _profile_moxfield(MOX_PAGE)
    assert len(decks) == 1                       # private + url-less skipped
    d = decks[0]
    assert d == ProfileDeck(name="Atraxa Superfriends",
                            url="https://moxfield.com/decks/abc123",
                            format="commander", count=99)


def test_profile_moxfield_builds_url_from_public_id():
    decks = _profile_moxfield({"data": [{"name": "X", "publicId": "zzz", "visibility": "public"}]})
    assert decks[0].url == "https://moxfield.com/decks/zzz" and decks[0].count == 0


def test_profile_archidekt_maps_and_skips_private():
    decks = _profile_archidekt(ARCH_PAGE)
    assert len(decks) == 1                       # private + id-less skipped
    assert decks[0] == ProfileDeck(name="Elfball", url="https://archidekt.com/decks/42",
                                   format="Commander", count=100)


def test_profile_archidekt_unknown_format_blank():
    decks = _profile_archidekt({"results": [{"id": 7, "name": "Y", "deckFormat": 999, "size": 1}]})
    assert decks[0].format == ""


def test_detect_profile():
    assert detect_profile("https://moxfield.com/users/bob") == ("moxfield", "bob")
    assert detect_profile("https://archidekt.com/u/alice") == ("archidekt", "alice")
    assert detect_profile("bob") is None
    assert detect_profile("") is None


# --- fetch -------------------------------------------------------------------

def _client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_fetch_profile_decks_both_providers():
    def handler(request):
        u = str(request.url)
        if "moxfield" in u:
            assert "authorUserNames=bob" in u and "pageSize=" in u
            return httpx.Response(200, json=MOX_PAGE)
        assert "ownerUsername=bob" in u
        return httpx.Response(200, json=ARCH_PAGE)

    async with _client(handler) as c:
        mox = await fetch_profile_decks("moxfield", "bob", client=c)
        assert [d.name for d in mox] == ["Atraxa Superfriends"]
        arch = await fetch_profile_decks("archidekt", "bob", client=c)
        assert [d.name for d in arch] == ["Elfball"]


@pytest.mark.asyncio
async def test_fetch_profile_decks_errors():
    # Bad provider / blank username never hits the network.
    with pytest.raises(DeckImportError):
        await fetch_profile_decks("tappedout", "bob")
    with pytest.raises(DeckImportError):
        await fetch_profile_decks("moxfield", "  ")
    # HTTP error and an empty profile both surface a friendly message.
    async with _client(lambda r: httpx.Response(404)) as c:
        with pytest.raises(DeckImportError):
            await fetch_profile_decks("moxfield", "ghost", client=c)
    async with _client(lambda r: httpx.Response(200, json={"data": []})) as c:
        with pytest.raises(DeckImportError):
            await fetch_profile_decks("moxfield", "empty", client=c)
    async with _client(lambda r: httpx.Response(500)) as c:
        with pytest.raises(DeckImportError):
            await fetch_profile_decks("archidekt", "boom", client=c)


@pytest.mark.asyncio
async def test_fetch_profile_own_client_created_and_closed(monkeypatch):
    # No client passed -> fetch_profile_decks builds its own AsyncClient and closes it.
    from src import deck_import
    closed = {"v": False}
    real = httpx.AsyncClient

    def factory(*a, **kw):
        c = real(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=MOX_PAGE)))
        orig_close = c.aclose

        async def track():
            closed["v"] = True
            await orig_close()

        c.aclose = track
        return c

    monkeypatch.setattr(deck_import.httpx, "AsyncClient", factory)
    decks = await fetch_profile_decks("moxfield", "bob")
    assert [d.name for d in decks] == ["Atraxa Superfriends"]
    assert closed["v"] is True


@pytest.mark.asyncio
async def test_fetch_profile_decks_network_error():
    def boom(request):
        raise httpx.ConnectError("down")

    async with _client(boom) as c:
        with pytest.raises(DeckImportError):
            await fetch_profile_decks("moxfield", "bob", client=c)


# --- routes ------------------------------------------------------------------

@pytest.mark.asyncio
async def test_import_profile_lists_decks(client, monkeypatch):
    async def fake(provider, username, **kw):
        return [ProfileDeck("Elfball", "https://archidekt.com/decks/42", "Commander", 100)]

    monkeypatch.setattr("src.routes.decks.fetch_profile_decks", fake)
    resp = await client.post("/decks/import-profile",
                             data={"provider": "archidekt", "who": "bob", "ownership": "full"})
    assert resp.status_code == 200
    assert "Elfball" in resp.text and 'name="urls"' in resp.text
    assert "fully owned" in resp.text.lower()


@pytest.mark.asyncio
async def test_import_profile_url_overrides_provider(client, monkeypatch):
    seen = {}

    async def fake(provider, username, **kw):
        seen.update(provider=provider, username=username)
        return [ProfileDeck("D", "https://moxfield.com/decks/x", "", 0)]

    monkeypatch.setattr("src.routes.decks.fetch_profile_decks", fake)
    # The dropdown says archidekt, but a Moxfield profile URL wins.
    await client.post("/decks/import-profile",
                      data={"provider": "archidekt", "who": "https://moxfield.com/users/bob"})
    assert seen == {"provider": "moxfield", "username": "bob"}


@pytest.mark.asyncio
async def test_import_profile_error_rerenders_form(client, monkeypatch):
    async def boom(provider, username, **kw):
        raise DeckImportError("No public decks found.")

    monkeypatch.setattr("src.routes.decks.fetch_profile_decks", boom)
    resp = await client.post("/decks/import-profile", data={"provider": "moxfield", "who": "x"})
    assert resp.status_code == 200 and "No public decks found." in resp.text


@pytest.mark.asyncio
async def test_import_profile_confirm_bulk_imports(client, session, monkeypatch):
    async def fake_fetch(url, **kw):
        if "bad" in url:
            raise DeckImportError("nope")
        return f"Deck {url[-1]}", "1 Sol Ring"

    monkeypatch.setattr("src.routes.decks.fetch_deck_from_url", fake_fetch)
    resp = await client.post(
        "/decks/import-profile/confirm",
        data={"urls": ["https://moxfield.com/decks/1", "https://moxfield.com/decks/bad",
                       "https://moxfield.com/decks/2"], "ownership": "unowned"},
        follow_redirects=False)
    assert resp.status_code == 303 and resp.headers["location"] == "/collection?tab=decks"
    # The two good decks imported; the failing one was skipped, not fatal.
    assert await session.scalar(select(func.count()).select_from(Deck)) == 2


@pytest.mark.asyncio
async def test_import_profile_confirm_full_ownership_adds_to_collection(client, session,
                                                                        monkeypatch):
    import uuid

    from src.models import Card, CollectionCard
    from src.scryfall.mapping import card_to_columns
    card = Card(**card_to_columns({"id": str(uuid.uuid4()), "oracle_id": str(uuid.uuid4()),
                                   "name": "Sol Ring", "set": "tst", "collector_number": "1"}))
    session.add(card)
    await session.commit()

    async def fake_fetch(url, **kw):
        return "Owned Deck", "2 Sol Ring"

    monkeypatch.setattr("src.routes.decks.fetch_deck_from_url", fake_fetch)
    await client.post("/decks/import-profile/confirm",
                      data={"urls": ["https://moxfield.com/decks/1"], "ownership": "full"},
                      follow_redirects=False)
    owned = await session.scalar(
        select(func.coalesce(func.sum(CollectionCard.quantity), 0))
        .where(CollectionCard.scryfall_id == card.scryfall_id))
    assert owned == 2


@pytest.mark.asyncio
async def test_profile_routes_read_only(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "read_only", True)
    assert (await client.post("/decks/import-profile",
                              data={"provider": "moxfield", "who": "x"})).status_code == 403
    assert (await client.post("/decks/import-profile/confirm",
                              data={"urls": ["x"]})).status_code == 403
