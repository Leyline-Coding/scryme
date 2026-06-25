"""HTTP client for the Scryfall API and CDN, enforcing their usage policy.

Policy (https://scryfall.com/docs/api):
  * every request sends a ``User-Agent`` and ``Accept`` header;
  * requests are throttled to stay under 10/s (default 100ms spacing);
  * an HTTP 429 locks the caller out for ~30s, so we back off and retry.

Downloads use ``aiter_raw`` to stream the *undecoded* body to disk — the bulk file is gzip and
we want to keep it compressed on disk rather than inflate ~2GB in memory.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import structlog

from src.config import Settings, get_settings

log = structlog.get_logger()


class ScryfallError(RuntimeError):
    """Raised when Scryfall returns a non-retryable error."""


class ScryfallClient:
    """Throttled async client. Use as an async context manager."""

    def __init__(self, settings: Settings | None = None, client: httpx.AsyncClient | None = None):
        self._settings = settings or get_settings()
        self._client = client
        self._owns_client = client is None
        self._lock = asyncio.Lock()
        self._last_request = 0.0

    @property
    def _headers(self) -> dict[str, str]:
        return {
            "User-Agent": self._settings.scryfall_user_agent,
            "Accept": self._settings.scryfall_accept,
        }

    async def __aenter__(self) -> ScryfallClient:
        if self._client is None:
            self._client = httpx.AsyncClient(headers=self._headers, timeout=60.0)
        return self

    async def __aexit__(self, *exc) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    async def _throttle(self) -> None:
        """Space requests at least ``scryfall_min_request_interval`` apart."""
        async with self._lock:
            wait = self._settings.scryfall_min_request_interval - (
                time.monotonic() - self._last_request
            )
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_request = time.monotonic()

    async def get_json(self, url: str, *, max_retries: int = 3) -> dict:
        """GET a JSON document, retrying once per 429 with a 30s backoff."""
        assert self._client is not None, "use 'async with ScryfallClient()'"
        for attempt in range(max_retries + 1):
            await self._throttle()
            resp = await self._client.get(url)
            if resp.status_code == 429:
                log.warning("scryfall.rate_limited", url=url, attempt=attempt)
                await asyncio.sleep(30)
                continue
            if resp.status_code >= 400:
                raise ScryfallError(f"GET {url} -> {resp.status_code}: {resp.text[:200]}")
            return resp.json()
        raise ScryfallError(f"GET {url} still rate-limited after {max_retries} retries")

    async def list_bulk_data(self) -> list[dict]:
        """Return the bulk-data catalog (oracle_cards, default_cards, ...)."""
        payload = await self.get_json(f"{self._settings.scryfall_api_base}/bulk-data")
        return payload.get("data", [])

    async def get_bulk_entry(self, bulk_type: str = "default_cards") -> dict:
        """Return the catalog entry for a bulk type (raises if absent)."""
        for entry in await self.list_bulk_data():
            if entry.get("type") == bulk_type:
                return entry
        raise ScryfallError(f"bulk type {bulk_type!r} not found in Scryfall catalog")

    async def download_to_file(self, url: str, dest: Path) -> Path:
        """Stream the *raw* (still-gzip) body of ``url`` to ``dest``."""
        assert self._client is not None, "use 'async with ScryfallClient()'"
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(dest.suffix + ".part")
        await self._throttle()
        async with self._client.stream("GET", url) as resp:
            if resp.status_code >= 400:
                raise ScryfallError(f"GET {url} -> {resp.status_code}")
            with tmp.open("wb") as fh:
                async for chunk in resp.aiter_raw():
                    fh.write(chunk)
        tmp.replace(dest)
        return dest

    @asynccontextmanager
    async def stream_bytes(self, url: str) -> AsyncIterator[AsyncIterator[bytes]]:
        """Yield an async iterator over the raw body bytes of ``url``."""
        assert self._client is not None, "use 'async with ScryfallClient()'"
        await self._throttle()
        async with self._client.stream("GET", url) as resp:
            if resp.status_code >= 400:
                raise ScryfallError(f"GET {url} -> {resp.status_code}")
            yield resp.aiter_raw()
