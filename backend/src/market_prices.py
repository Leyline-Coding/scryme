"""Preferred-marketplace prices (#231): Card Kingdom + ManaPool, cached in ``cards.market_prices``.

Scryfall only gives ``usd`` (TCGplayer market) / ``eur`` (Cardmarket) / ``tix``. The other two
marketplaces come from their own feeds and are cached per printing so display stays a fast column
read:

* **Card Kingdom** — MTGJSON (MIT-licensed) ``AllPricesToday`` (~5 MB/day), keyed by MTGJSON UUID.
  We join it to our cards via ``cards.mtgjson_id``, which is backfilled *once* from MTGJSON
  ``AllIdentifiers`` (~225 MB, changes only when sets release — run ``backfill-mtgjson-ids``).
* **ManaPool** — the public ``/api/v1/prices/singles`` feed (~50 MB/day), keyed natively by
  ``scryfall_id`` (no credentials, no mapping). Only in-stock singles are listed.

Everything degrades gracefully: a source that's unreachable (or, for Card Kingdom, not yet mapped)
just leaves its prices absent and the app falls back to TCGplayer. Prices are stored as strings to
match Scryfall's ``prices`` convention, e.g. ``{"cardkingdom": {"usd": "1.23", "usd_foil": "4.56"},
"manapool": {"usd": "1.84", "usd_foil": "25.00"}}``.
"""

from __future__ import annotations

import datetime
import gzip
import json
import tempfile
from collections.abc import Iterator
from pathlib import Path

import aiofiles
import httpx
import ijson
import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from src.config import get_settings
from src.db import SessionLocal
from src.models import IngestState

log = structlog.get_logger()

PRICE_SOURCES = ("tcgplayer", "cardkingdom", "manapool")

MTGJSON_BASE = "https://mtgjson.com/api/v5"
MANAPOOL_SINGLES_URL = "https://manapool.com/api/v1/prices/singles"
# ingest_state rows guarding the daily re-download (reuses the Scryfall 24h-cache machinery).
CK_STATE = "mtgjson_cardkingdom"
MANAPOOL_STATE = "manapool_prices"
IDMAP_STATE = "mtgjson_identifiers"

_REFRESH_HOURS = 20  # allow a daily refresh without being blocked by a slightly-early run


def _ua() -> str:
    return get_settings().scryfall_user_agent


def _guard_allows(state: IngestState | None) -> bool:
    if state is None or state.last_downloaded_at is None:
        return True
    age = datetime.datetime.now(datetime.UTC) - state.last_downloaded_at
    return age >= datetime.timedelta(hours=_REFRESH_HOURS)


async def _record(bulk_type: str, count: int) -> None:
    async with SessionLocal() as s:
        state = await s.get(IngestState, bulk_type)
        if state is None:
            state = IngestState(bulk_type=bulk_type)
            s.add(state)
        state.last_downloaded_at = datetime.datetime.now(datetime.UTC)
        state.card_count = count
        state.status = "idle"
        await s.commit()


# --- price extraction (pure, unit-tested) --------------------------------------------------

def _latest(day_map: dict | None) -> float | None:
    """The most recent price from an MTGJSON ``{date: price}`` map."""
    if not day_map:
        return None
    return day_map[max(day_map)]


def cardkingdom_price(price_obj: dict) -> dict | None:
    """Extract Card Kingdom retail normal/foil from one MTGJSON price entry (or None)."""
    ck = ((price_obj or {}).get("paper") or {}).get("cardkingdom") or {}
    retail = ck.get("retail") or {}
    normal = _latest(retail.get("normal"))
    foil = _latest(retail.get("foil"))
    out: dict[str, str] = {}
    if normal is not None:
        out["usd"] = f"{float(normal):.2f}"
    if foil is not None:
        out["usd_foil"] = f"{float(foil):.2f}"
    return out or None


def _cents(value) -> str | None:
    return f"{value / 100:.2f}" if isinstance(value, int | float) else None


def manapool_price(row: dict) -> dict | None:
    """Map one ManaPool singles row to normal/foil/etched USD prices (prefers market price)."""
    out: dict[str, str] = {}
    normal = _cents(row.get("price_market")) or _cents(row.get("price_cents"))
    foil = _cents(row.get("price_market_foil")) or _cents(row.get("price_cents_foil"))
    etched = _cents(row.get("price_cents_etched"))
    if normal:
        out["usd"] = normal
    if foil:
        out["usd_foil"] = foil
    if etched:
        out["usd_etched"] = etched
    return out or None


# --- bulk DB writes ------------------------------------------------------------------------

async def _apply_prices(
    source: str, by_scryfall: dict[str, dict], session_factory: async_sessionmaker
) -> int:
    """Merge ``{scryfall_id: prices}`` into ``cards.market_prices[source]`` in batches."""
    if not by_scryfall:
        return 0
    stmt = text(
        "UPDATE cards AS c SET market_prices = jsonb_set("
        "COALESCE(c.market_prices, '{}'::jsonb), CAST(:keypath AS text[]), "
        "CAST(:val AS jsonb), true) "
        "WHERE c.scryfall_id = CAST(:sid AS uuid)"
    )
    keypath = [source]  # asyncpg binds a Python list to a Postgres text[] (the jsonb_set path)
    written = 0
    batch: list[dict] = []
    async with session_factory() as session:
        for sid, prices in by_scryfall.items():
            batch.append({"keypath": keypath, "val": json.dumps(prices), "sid": str(sid)})
            if len(batch) >= 1000:
                await session.execute(stmt, batch)
                written += len(batch)
                batch = []
        if batch:
            await session.execute(stmt, batch)
            written += len(batch)
        await session.commit()
    return written


# --- downloads -----------------------------------------------------------------------------

async def _download(url: str, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    async with httpx.AsyncClient(headers={"User-Agent": _ua()}, timeout=120.0,
                                 follow_redirects=True) as client:
        async with client.stream("GET", url) as resp:
            resp.raise_for_status()
            async with aiofiles.open(tmp, "wb") as fh:
                async for chunk in resp.aiter_bytes():
                    await fh.write(chunk)
    tmp.replace(dest)
    return dest


def _stream_kv(path: Path, prefix: str) -> Iterator[tuple[str, dict]]:
    """Stream ``(key, value)`` pairs under ``prefix`` from a (gzip) JSON object, memory-flat."""
    with gzip.open(path, "rb") as fh:
        yield from ijson.kvitems(fh, prefix, use_float=True)


# --- sync entry points ---------------------------------------------------------------------

async def backfill_mtgjson_ids(
    session_factory: async_sessionmaker = SessionLocal, *, force: bool = False
) -> int:
    """Populate ``cards.mtgjson_id`` from MTGJSON AllIdentifiers (one-time / on new sets)."""
    async with session_factory() as s:
        if not force and not _guard_allows(await s.get(IngestState, IDMAP_STATE)):
            log.info("market.idmap.skip_cache")
            return 0
    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp) / "AllIdentifiers.json.gz"
        await _download(f"{MTGJSON_BASE}/AllIdentifiers.json.gz", dest)
        mapping: dict[str, str] = {}
        for uuid, obj in _stream_kv(dest, "data"):
            sid = (obj.get("identifiers") or {}).get("scryfallId")
            if sid:
                mapping[sid] = uuid
    stmt = text("UPDATE cards SET mtgjson_id = :uuid WHERE scryfall_id = CAST(:sid AS uuid)")
    written = 0
    batch: list[dict] = []
    async with session_factory() as session:
        for sid, uuid in mapping.items():
            batch.append({"uuid": uuid, "sid": sid})
            if len(batch) >= 1000:
                await session.execute(stmt, batch)
                written += len(batch)
                batch = []
        if batch:
            await session.execute(stmt, batch)
            written += len(batch)
        await session.commit()
    await _record(IDMAP_STATE, written)
    log.info("market.idmap.done", mapped=written)
    return written


async def sync_cardkingdom(
    session_factory: async_sessionmaker = SessionLocal, *, force: bool = False
) -> int:
    """Sync Card Kingdom retail prices from MTGJSON into ``market_prices['cardkingdom']``."""
    async with session_factory() as s:
        if not force and not _guard_allows(await s.get(IngestState, CK_STATE)):
            log.info("market.cardkingdom.skip_cache")
            return 0
        # Build uuid -> scryfall_id from previously-backfilled ids.
        rows = await s.execute(text("SELECT mtgjson_id, scryfall_id FROM cards "
                                    "WHERE mtgjson_id IS NOT NULL"))
        uuid_to_sid = {u: str(sid) for u, sid in rows}
    if not uuid_to_sid:
        log.warning("market.cardkingdom.no_idmap")  # run backfill-mtgjson-ids first
        return 0
    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp) / "AllPricesToday.json.gz"
        await _download(f"{MTGJSON_BASE}/AllPricesToday.json.gz", dest)
        by_scryfall: dict[str, dict] = {}
        for uuid, price_obj in _stream_kv(dest, "data"):
            sid = uuid_to_sid.get(uuid)
            if not sid:
                continue
            ck = cardkingdom_price(price_obj)
            if ck:
                by_scryfall[sid] = ck
    written = await _apply_prices("cardkingdom", by_scryfall, session_factory)
    await _record(CK_STATE, written)
    log.info("market.cardkingdom.done", priced=written)
    return written


async def sync_manapool(
    session_factory: async_sessionmaker = SessionLocal, *, force: bool = False
) -> int:
    """Sync ManaPool prices from its public singles feed into ``market_prices['manapool']``."""
    async with session_factory() as s:
        if not force and not _guard_allows(await s.get(IngestState, MANAPOOL_STATE)):
            log.info("market.manapool.skip_cache")
            return 0
    async with httpx.AsyncClient(headers={"User-Agent": _ua()}, timeout=120.0,
                                 follow_redirects=True) as client:
        resp = await client.get(MANAPOOL_SINGLES_URL)
        resp.raise_for_status()
        payload = resp.json()
    by_scryfall: dict[str, dict] = {}
    for row in payload.get("data") or []:
        sid = row.get("scryfall_id")
        if not sid:
            continue
        mp = manapool_price(row)
        if mp:
            by_scryfall[sid] = mp
    written = await _apply_prices("manapool", by_scryfall, session_factory)
    await _record(MANAPOOL_STATE, written)
    log.info("market.manapool.done", priced=written)
    return written


async def sync_market_prices(
    session_factory: async_sessionmaker = SessionLocal, *, force: bool = False
) -> dict[str, int]:
    """Refresh both alternate price sources; each failure is isolated (logged, others proceed)."""
    result: dict[str, int] = {}
    for name, fn in (("cardkingdom", sync_cardkingdom), ("manapool", sync_manapool)):
        try:
            result[name] = await fn(session_factory, force=force)
        except Exception as exc:  # noqa: BLE001 — one bad source shouldn't sink the others
            log.warning("market.sync.source_failed", source=name, error=str(exc))
            result[name] = 0
    return result
