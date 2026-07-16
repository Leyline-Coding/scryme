"""Coverage tests for src.deck_import: parse error paths, empty fetches, own-client close."""

import httpx
import pytest
from src import deck_import
from src.deck_import import (
    SUPPORTED,
    DeckImportError,
    detect_host,
    fetch_deck_from_url,
    parse_archidekt,
    parse_moxfield,
)


def test_detect_host_and_supported():
    assert detect_host("https://moxfield.com/decks/abc") == "moxfield"
    assert detect_host("https://archidekt.com/decks/1/x") == "archidekt"
    assert detect_host("https://tappedout.net/mtg-decks/x/") == "tappedout"
    assert detect_host("https://example.com/x") is None
    assert SUPPORTED


def test_parse_moxfield_and_archidekt_happy():
    name, text = parse_moxfield({
        "name": " Deck ", "commanders": {"Atraxa": {"quantity": 1}},
        "mainboard": {"Forest": {"quantity": 10}}, "sideboard": {"Duress": {"quantity": 2}},
    })
    assert name == "Deck" and "1 Atraxa" in text and "10 Forest" in text
    assert "Sideboard" in text and "2 Duress" in text
    _, atext = parse_archidekt({
        "name": "E", "cards": [
            {"quantity": 1, "card": {"oracleCard": {"name": "Elf"}}, "categories": ["Ramp"]},
            {"quantity": 1, "card": {"oracleCard": {"name": "Maybe"}},
             "categories": ["Maybeboard"]},
        ],
    })
    main, _, side = atext.partition("Sideboard")
    assert "1 Elf" in main and "1 Maybe" in side


@pytest.mark.asyncio
async def test_fetch_each_host_and_unsupported():
    def handler(request):
        u = str(request.url)
        if "moxfield" in u:
            return httpx.Response(
                200, json={"name": "M", "mainboard": {"Sol Ring": {"quantity": 1}}})
        if "archidekt" in u:
            return httpx.Response(200, json={"name": "A",
                "cards": [{"quantity": 1, "card": {"oracleCard": {"name": "Elf"}}}]})
        return httpx.Response(200, text="1 Forest")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
        assert (await fetch_deck_from_url("https://moxfield.com/decks/x", client=c))[0] == "M"
        assert (await fetch_deck_from_url("https://archidekt.com/decks/1/x", client=c))[0] == "A"
        name, text = await fetch_deck_from_url("https://tappedout.net/mtg-decks/my-deck/", client=c)
        assert name == "My Deck" and "1 Forest" in text
    with pytest.raises(DeckImportError):
        await fetch_deck_from_url("https://example.com/x")


@pytest.mark.asyncio
async def test_http_status_error_wrapped():
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(404))
    ) as c:
        with pytest.raises(DeckImportError) as e:
            await fetch_deck_from_url("https://moxfield.com/decks/missing", client=c)
        assert "404" in str(e.value)


def test_parse_moxfield_empty_raises():
    with pytest.raises(DeckImportError):
        parse_moxfield({"name": "Empty", "mainboard": {}})


def test_parse_archidekt_skips_nameless_and_empty():
    # An entry with no name is skipped; if that leaves nothing, it raises.
    with pytest.raises(DeckImportError):
        parse_archidekt({"name": "X", "cards": [{"quantity": 1, "card": {}}]})


def test_parse_archidekt_name_from_card_fallback():
    name, text = parse_archidekt(
        {"name": "N", "cards": [{"quantity": 2, "card": {"name": "Direct Name"}}]}
    )
    assert "2 Direct Name" in text and name == "N"


@pytest.mark.asyncio
async def test_tappedout_empty_raises():
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, text="   "))
    ) as c:
        with pytest.raises(DeckImportError):
            await fetch_deck_from_url("https://tappedout.net/mtg-decks/x/", client=c)


@pytest.mark.asyncio
async def test_network_error_wrapped():
    def handler(request):
        raise httpx.ConnectError("boom")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
        with pytest.raises(DeckImportError) as e:
            await fetch_deck_from_url("https://moxfield.com/decks/x", client=c)
        assert "reach" in str(e.value)


@pytest.mark.asyncio
async def test_own_client_created_and_closed(monkeypatch):
    # No client passed -> fetch builds its own AsyncClient and closes it in `finally`.
    closed = {"v": False}

    def handler(request):
        return httpx.Response(
            200, json={"name": "Deck", "mainboard": {"Sol Ring": {"quantity": 1}}})

    real = httpx.AsyncClient

    def factory(*a, **kw):
        client = real(transport=httpx.MockTransport(handler))
        orig_close = client.aclose

        async def track():
            closed["v"] = True
            await orig_close()

        client.aclose = track
        return client

    monkeypatch.setattr(deck_import.httpx, "AsyncClient", factory)
    name, text = await fetch_deck_from_url("https://moxfield.com/decks/x")
    assert name == "Deck" and "1 Sol Ring" in text
    assert closed["v"] is True
