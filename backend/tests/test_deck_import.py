"""Import a deck from a URL (#98): host detection, per-host parsing, fetch, and the route."""

import httpx
import pytest
from src import deck_import
from src.deck_import import (
    DeckImportError,
    detect_host,
    fetch_deck_from_url,
    parse_archidekt,
    parse_moxfield,
)


def test_parse_moxfield_keeps_printing_and_finish():
    """The exact printing + finish the deck specifies survive into the decklist text."""
    payload = {"name": "D", "mainboard": {
        "Sol Ring": {"quantity": 1, "finish": "foil", "card": {"set": "cmr", "cn": "1"}},
        "Command Tower": {"quantity": 1, "finish": "etched",
                          "card": {"set": "cmr", "cn": "350"}},
        "Island": {"quantity": 2, "finish": "nonFoil", "card": {"set": "znr", "cn": "270"}},
        "Mystery Card": {"quantity": 1},   # no card block -> plain line
    }}
    _name, text = parse_moxfield(payload)
    assert "1 Sol Ring (CMR) 1 *F*" in text
    assert "1 Command Tower (CMR) 350 *E*" in text
    assert "2 Island (ZNR) 270" in text and "2 Island (ZNR) 270 *F*" not in text
    assert "1 Mystery Card" in text


def test_parse_archidekt_keeps_printing_and_finish():
    payload = {"name": "D", "cards": [
        {"quantity": 1, "modifier": "Foil", "card": {
            "oracleCard": {"name": "Sol Ring"}, "edition": {"editioncode": "cmr"},
            "collectorNumber": "1"}},
        {"quantity": 1, "modifier": "Etched", "card": {
            "oracleCard": {"name": "Command Tower"}, "edition": {"editioncode": "cmr"},
            "collectorNumber": "350"}},
        {"quantity": 3, "modifier": "Normal", "card": {
            "oracleCard": {"name": "Island"}, "edition": {"editioncode": "znr"},
            "collectorNumber": "270"}},
    ]}
    _name, text = parse_archidekt(payload)
    assert "1 Sol Ring (CMR) 1 *F*" in text
    assert "1 Command Tower (CMR) 350 *E*" in text
    assert "3 Island (ZNR) 270" in text


MOX = {
    "name": "Atraxa Superfriends",
    "commanders": {"Atraxa, Praetors' Voice": {"quantity": 1}},
    "mainboard": {"Sol Ring": {"quantity": 1}, "Forest": {"quantity": 10}},
    "sideboard": {"Pithing Needle": {"quantity": 2}},
}
ARCH = {
    "name": "Elfball",
    "cards": [
        {"quantity": 1, "card": {"oracleCard": {"name": "Llanowar Elves"}}, "categories": ["Ramp"]},
        {"quantity": 1, "card": {"oracleCard": {"name": "Maybe This"}},
         "categories": ["Maybeboard"]},
    ],
}


def test_detect_host():
    assert detect_host("https://www.moxfield.com/decks/abc123") == "moxfield"
    assert detect_host("https://archidekt.com/decks/98765/elfball") == "archidekt"
    assert detect_host("https://tappedout.net/mtg-decks/my-deck/") == "tappedout"
    assert detect_host("https://example.com/deck") is None


def test_parse_moxfield():
    name, text = parse_moxfield(MOX)
    assert name == "Atraxa Superfriends"
    assert "1 Atraxa, Praetors' Voice" in text
    assert "10 Forest" in text and "1 Sol Ring" in text
    assert "Sideboard" in text and "2 Pithing Needle" in text


def test_parse_archidekt():
    name, text = parse_archidekt(ARCH)
    assert name == "Elfball"
    main, _, side = text.partition("Sideboard")
    assert "1 Llanowar Elves" in main
    assert "1 Maybe This" in side  # maybeboard → sideboard


def _mock_client():
    def handler(request: httpx.Request) -> httpx.Response:
        u = str(request.url)
        if "moxfield" in u:
            return httpx.Response(200, json=MOX)
        if "archidekt" in u:
            return httpx.Response(200, json=ARCH)
        if "tappedout" in u:
            return httpx.Response(200, text="1 Forest\n1 Llanowar Elves")
        return httpx.Response(404)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_fetch_each_host():
    async with _mock_client() as c:
        name, text = await fetch_deck_from_url("https://moxfield.com/decks/x", client=c)
        assert name == "Atraxa Superfriends" and "1 Sol Ring" in text
        name, text = await fetch_deck_from_url("https://archidekt.com/decks/42/x", client=c)
        assert name == "Elfball"
        name, text = await fetch_deck_from_url("https://tappedout.net/mtg-decks/my-deck/", client=c)
        assert name == "My Deck" and "1 Forest" in text


@pytest.mark.asyncio
async def test_fetch_unsupported_and_404():
    with pytest.raises(DeckImportError):
        await fetch_deck_from_url("https://example.com/deck")
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(404))
    ) as c:
        with pytest.raises(DeckImportError):
            await fetch_deck_from_url("https://moxfield.com/decks/missing", client=c)


@pytest.mark.asyncio
async def test_import_route(client, monkeypatch):
    async def fake_fetch(url, **kw):
        return "Imported", "1 Sol Ring\n1 Forest"

    monkeypatch.setattr("src.routes.decks.fetch_deck_from_url", fake_fetch)
    resp = await client.post(
        "/decks/import-url", data={"url": "https://moxfield.com/decks/x"}, follow_redirects=False
    )
    assert resp.status_code == 303 and resp.headers["location"].startswith("/decks/")


@pytest.mark.asyncio
async def test_import_route_error(client, monkeypatch):
    async def boom(url, **kw):
        raise DeckImportError("nope")

    monkeypatch.setattr("src.routes.decks.fetch_deck_from_url", boom)
    resp = await client.post("/decks/import-url", data={"url": "bad"})
    assert resp.status_code == 200 and "nope" in resp.text


def test_module_exports():
    assert deck_import.SUPPORTED
