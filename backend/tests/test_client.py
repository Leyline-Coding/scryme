"""ScryfallClient tests: policy headers, bulk lookup, 429 backoff, and download.

Uses httpx.MockTransport so nothing actually hits the network. asyncio.sleep is patched to a
no-op so the 30s rate-limit backoff doesn't slow the suite.
"""

import httpx
import pytest
import src.scryfall.client as client_mod
from src.scryfall.client import ScryfallClient, ScryfallError


async def _noop(*args, **kwargs):
    return None


def _injected(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_sets_policy_headers():
    async with ScryfallClient() as sc:
        assert "scryme" in sc._client.headers["user-agent"].lower()
        assert sc._client.headers["accept"].startswith("application/json")


@pytest.mark.asyncio
async def test_list_and_get_bulk_entry():
    seen_headers = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen_headers.update(request.headers)
        return httpx.Response(
            200,
            json={
                "object": "list",
                "data": [
                    {"type": "oracle_cards", "download_uri": "https://x/o.json"},
                    {"type": "default_cards", "download_uri": "https://x/d.json",
                     "updated_at": "2026-06-25T09:00:00+00:00"},
                ],
            },
        )

    inner = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        headers={"User-Agent": "scryme/test", "Accept": "application/json"},
    )
    async with ScryfallClient(client=inner) as sc:
        entry = await sc.get_bulk_entry("default_cards")
        assert entry["download_uri"].endswith("d.json")
        with pytest.raises(ScryfallError):
            await sc.get_bulk_entry("does_not_exist")
    assert "user-agent" in seen_headers


@pytest.mark.asyncio
async def test_429_then_success(monkeypatch):
    monkeypatch.setattr(client_mod.asyncio, "sleep", _noop)
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429)
        return httpx.Response(200, json={"ok": True})

    async with ScryfallClient(client=_injected(handler)) as sc:
        data = await sc.get_json("https://api.scryfall.test/thing")
        assert data == {"ok": True}
        assert calls["n"] == 2  # retried once after the 429


@pytest.mark.asyncio
async def test_persistent_429_raises(monkeypatch):
    monkeypatch.setattr(client_mod.asyncio, "sleep", _noop)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429)

    async with ScryfallClient(client=_injected(handler)) as sc:
        with pytest.raises(ScryfallError):
            await sc.get_json("https://api.scryfall.test/thing", max_retries=1)


@pytest.mark.asyncio
async def test_get_json_http_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="not found")

    async with ScryfallClient(client=_injected(handler)) as sc:
        with pytest.raises(ScryfallError):
            await sc.get_json("https://api.scryfall.test/missing")


@pytest.mark.asyncio
async def test_download_to_file(tmp_path):
    async def body():
        yield b"BULK"
        yield b"DATA"

    def handler(request: httpx.Request) -> httpx.Response:
        # An async generator body yields an AsyncByteStream so aiter_raw() can stream it.
        return httpx.Response(200, content=body())

    async with ScryfallClient(client=_injected(handler)) as sc:
        dest = tmp_path / "nested" / "cards.json.gz"
        await sc.download_to_file("https://data.scryfall.test/file", dest)
        assert dest.read_bytes() == b"BULKDATA"
        assert not dest.with_suffix(dest.suffix + ".part").exists()  # temp cleaned up
