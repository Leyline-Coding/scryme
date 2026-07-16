"""Coverage tests for src/scryfall/client.py — throttle, streaming, download errors, headers.

Uses httpx.MockTransport so nothing hits the network; asyncio.sleep is patched to a no-op so the
throttle spacing and the 30s 429 backoff don't slow the suite.
"""

from __future__ import annotations

import httpx
import pytest
import src.scryfall.client as client_mod
from src.scryfall.client import ScryfallClient, ScryfallError


async def _noop(*a, **k):
    return None


def _injected(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_owns_client_closes_on_exit():
    async with ScryfallClient() as sc:
        assert sc._client is not None
        inner = sc._client
    assert sc._client is None  # owned client is closed and cleared
    assert inner.is_closed


@pytest.mark.asyncio
async def test_injected_client_not_closed_on_exit():
    inner = _injected(lambda r: httpx.Response(200, json={}))
    async with ScryfallClient(client=inner) as sc:
        assert sc._client is inner
    # We don't own it, so it stays open and set.
    assert sc._client is inner
    assert not inner.is_closed
    await inner.aclose()


@pytest.mark.asyncio
async def test_throttle_sleeps_when_called_rapidly(monkeypatch):
    slept = []

    async def fake_sleep(seconds):
        slept.append(seconds)

    monkeypatch.setattr(client_mod.asyncio, "sleep", fake_sleep)
    # Force a large min interval so the second call must wait.
    async with ScryfallClient(client=_injected(lambda r: httpx.Response(200, json={}))) as sc:
        monkeypatch.setattr(sc._settings, "scryfall_min_request_interval", 10.0)
        await sc.get_json("https://api.test/a")
        await sc.get_json("https://api.test/b")
    assert any(s > 0 for s in slept)  # the second request was throttled


@pytest.mark.asyncio
async def test_list_bulk_data_empty_payload():
    async with ScryfallClient(client=_injected(lambda r: httpx.Response(200, json={}))) as sc:
        assert await sc.list_bulk_data() == []  # missing "data" key -> []


@pytest.mark.asyncio
async def test_get_json_429_then_success(monkeypatch):
    monkeypatch.setattr(client_mod.asyncio, "sleep", _noop)
    n = {"c": 0}

    def handler(request):
        n["c"] += 1
        return httpx.Response(429) if n["c"] == 1 else httpx.Response(200, json={"ok": 1})

    async with ScryfallClient(client=_injected(handler)) as sc:
        assert await sc.get_json("https://api.test/x") == {"ok": 1}
        assert n["c"] == 2


@pytest.mark.asyncio
async def test_get_json_persistent_429_raises(monkeypatch):
    monkeypatch.setattr(client_mod.asyncio, "sleep", _noop)
    async with ScryfallClient(client=_injected(lambda r: httpx.Response(429))) as sc:
        with pytest.raises(ScryfallError, match="rate-limited"):
            await sc.get_json("https://api.test/x", max_retries=1)


@pytest.mark.asyncio
async def test_get_json_http_error_raises():
    async with ScryfallClient(client=_injected(lambda r: httpx.Response(404, text="nope"))) as sc:
        with pytest.raises(ScryfallError, match="404"):
            await sc.get_json("https://api.test/missing")


@pytest.mark.asyncio
async def test_get_bulk_entry_found_and_missing():
    def handler(request):
        return httpx.Response(200, json={"data": [
            {"type": "oracle_cards", "download_uri": "http://x/o.json"},
            {"type": "default_cards", "download_uri": "http://x/d.json"},
        ]})

    async with ScryfallClient(client=_injected(handler)) as sc:
        entry = await sc.get_bulk_entry("default_cards")
        assert entry["download_uri"].endswith("d.json")
        with pytest.raises(ScryfallError, match="not found"):
            await sc.get_bulk_entry("nope")


@pytest.mark.asyncio
async def test_download_success_streams_and_cleans_temp(tmp_path):
    async def body():
        yield b"BULK"
        yield b"DATA"

    handler = lambda r: httpx.Response(200, content=body())  # noqa: E731
    async with ScryfallClient(client=_injected(handler)) as sc:
        dest = tmp_path / "sub" / "cards.json.gz"
        out = await sc.download_to_file("https://data.test/f", dest)
        assert out == dest
        assert dest.read_bytes() == b"BULKDATA"
        assert not dest.with_suffix(dest.suffix + ".part").exists()


@pytest.mark.asyncio
async def test_get_json_requires_context_manager():
    sc = ScryfallClient()
    with pytest.raises(AssertionError):
        await sc.get_json("https://api.test/x")


@pytest.mark.asyncio
async def test_download_requires_context_manager(tmp_path):
    sc = ScryfallClient()
    with pytest.raises(AssertionError):
        await sc.download_to_file("https://api.test/x", tmp_path / "f")


@pytest.mark.asyncio
async def test_download_http_error_raises(tmp_path):
    def handler(request):
        return httpx.Response(500, text="boom")

    async with ScryfallClient(client=_injected(handler)) as sc:
        with pytest.raises(ScryfallError):
            await sc.download_to_file("https://data.test/f", tmp_path / "f.gz")


@pytest.mark.asyncio
async def test_stream_bytes_yields_body():
    async def body():
        yield b"AB"
        yield b"CD"

    def handler(request):
        return httpx.Response(200, content=body())

    chunks = bytearray()
    async with ScryfallClient(client=_injected(handler)) as sc:
        async with sc.stream_bytes("https://data.test/f") as stream:
            async for chunk in stream:
                chunks += chunk
    assert bytes(chunks) == b"ABCD"


@pytest.mark.asyncio
async def test_stream_bytes_http_error():
    def handler(request):
        return httpx.Response(404)

    async with ScryfallClient(client=_injected(handler)) as sc:
        with pytest.raises(ScryfallError):
            async with sc.stream_bytes("https://data.test/missing"):
                pass


@pytest.mark.asyncio
async def test_stream_bytes_requires_context_manager():
    sc = ScryfallClient()
    with pytest.raises(AssertionError):
        async with sc.stream_bytes("https://api.test/x"):
            pass
