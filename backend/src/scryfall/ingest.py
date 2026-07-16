"""Ingest the Scryfall *Default Cards* bulk file into the ``cards`` table.

Flow: check the 24h refresh guard, resolve the ``default_cards`` download URI, stream the gzip
file to disk, then stream-parse it with ijson and upsert in batches. Memory stays flat because
neither the download nor the parse holds the whole (~2GB uncompressed) document at once.
"""

from __future__ import annotations

import asyncio
import datetime
import gzip
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import ijson
import structlog
from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import async_sessionmaker

from src.config import Settings, get_settings
from src.db import SessionLocal
from src.models import Card, IngestState
from src.scryfall.client import ScryfallClient
from src.scryfall.mapping import card_to_columns

log = structlog.get_logger()

BATCH_SIZE = 1000
BULK_TYPE = "default_cards"

# Rough physical-card count, used only to animate the first-run progress bar (the exact total
# isn't known until the stream finishes). The live `ingested` count below is always exact.
ESTIMATED_TOTAL = 110_000

# Live ingest progress, polled via GET /admin/ingest/progress. In-memory is fine: the desktop app
# and self-host both run a single uvicorn process. phase: idle|downloading|ingesting|done|error.
_PROGRESS: dict = {"phase": "idle", "ingested": 0, "total": None, "error": None}


def get_ingest_progress() -> dict:
    """A snapshot of the current ingest progress."""
    return dict(_PROGRESS)


def _set_progress(**kw) -> None:
    _PROGRESS.update(kw)


@dataclass
class IngestResult:
    skipped: bool
    card_count: int
    source_updated_at: datetime.datetime | None
    reason: str | None = None


def _is_gzip(path: Path) -> bool:
    with path.open("rb") as fh:
        return fh.read(2) == b"\x1f\x8b"


def _is_paper(raw: dict) -> bool:
    """True unless the card is digital-only (Arena/MTGO). scryme is for physical collections."""
    games = raw.get("games")
    return not games or "paper" in games


def _read_batches(path: Path, batch_size: int = BATCH_SIZE) -> Iterator[list[dict]]:
    """Yield lists of raw card dicts from a (gzip or plain) JSON array file (paper cards only)."""
    opener = gzip.open if _is_gzip(path) else open
    with opener(path, "rb") as fh:
        batch: list[dict] = []
        # use_float keeps numbers as float (not Decimal), so raw is JSON-serializable for JSONB.
        for obj in ijson.items(fh, "item", use_float=True):
            if not _is_paper(obj):
                continue
            batch.append(obj)
            if len(batch) >= batch_size:
                yield batch
                batch = []
        if batch:
            yield batch


async def prune_digital_only(session_factory: async_sessionmaker = SessionLocal) -> int:
    """Delete already-ingested digital-only (Arena/MTGO) cards. Returns rows removed."""
    async with session_factory() as session:
        result = await session.execute(
            text(
                "DELETE FROM cards WHERE jsonb_typeof(raw->'games') = 'array' "
                "AND NOT (raw->'games' @> '[\"paper\"]'::jsonb)"
            )
        )
        await session.commit()
        return result.rowcount or 0


async def _upsert_batch(session, raw_cards: list[dict]) -> None:
    rows = [card_to_columns(c) for c in raw_cards]
    stmt = pg_insert(Card).values(rows)
    update_cols = {
        col.name: getattr(stmt.excluded, col.name)
        for col in Card.__table__.columns
        if col.name != "scryfall_id"
    }
    # Preserve a previously-cached image status across re-ingests.
    update_cols.pop("image_status", None)
    stmt = stmt.on_conflict_do_update(index_elements=["scryfall_id"], set_=update_cols)
    await session.execute(stmt)


async def ingest_from_path(
    path: Path,
    session_factory: async_sessionmaker = SessionLocal,
    batch_size: int = BATCH_SIZE,
) -> int:
    """Parse a bulk JSON file at ``path`` and upsert every card. Returns the count."""
    loop = asyncio.get_running_loop()
    batches = _read_batches(path, batch_size)
    total = 0
    while True:
        # Pull the next batch off the (blocking) parser in a worker thread.
        batch = await loop.run_in_executor(None, lambda: next(batches, None))
        if batch is None:
            break
        async with session_factory() as session:
            await _upsert_batch(session, batch)
            await session.commit()
        total += len(batch)
        _set_progress(ingested=total)
        log.info("scryfall.ingest.progress", cards=total)
    # Remove any digital-only cards left over from an earlier (unfiltered) ingest.
    removed = await prune_digital_only(session_factory)
    if removed:
        log.info("scryfall.ingest.pruned_digital", removed=removed)
    return total


def _parse_dt(value: str | None) -> datetime.datetime | None:
    if not value:
        return None
    return datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))


def _guard_allows_refresh(state: IngestState | None, source_updated: datetime.datetime | None,
                          min_hours: int) -> bool:
    """False when we already have this exact bulk and downloaded it within the cache window."""
    if state is None or state.last_downloaded_at is None:
        return True
    age = datetime.datetime.now(datetime.UTC) - state.last_downloaded_at
    if age >= datetime.timedelta(hours=min_hours):
        return True
    return state.source_updated_at != source_updated


async def ingest_default_cards(
    *,
    force: bool = False,
    client: ScryfallClient | None = None,
    settings: Settings | None = None,
) -> IngestResult:
    """Download (if needed) and ingest the Default Cards bulk file."""
    settings = settings or get_settings()

    async with SessionLocal() as s:
        state = await s.get(IngestState, BULK_TYPE)

    async with (client or ScryfallClient(settings)) as sc:
        entry = await sc.get_bulk_entry(BULK_TYPE)
        source_updated = _parse_dt(entry.get("updated_at"))

        if not force and not _guard_allows_refresh(
            state, source_updated, settings.bulk_refresh_min_hours
        ):
            log.info("scryfall.ingest.skipped", reason="within cache window")
            _set_progress(phase="done", error=None)
            return IngestResult(
                skipped=True,
                card_count=state.card_count if state else 0,
                source_updated_at=source_updated,
                reason="cached",
            )

        await _set_status("running")
        _set_progress(phase="downloading", ingested=0, total=ESTIMATED_TOTAL, error=None)
        dest = settings.data_dir / "default_cards.json.gz"
        log.info("scryfall.ingest.download", url=entry["download_uri"], dest=str(dest))
        await sc.download_to_file(entry["download_uri"], dest)

    try:
        _set_progress(phase="ingesting", total=ESTIMATED_TOTAL)
        count = await ingest_from_path(dest)
    except Exception as exc:
        await _set_status("error")
        _set_progress(phase="error", error=str(exc))
        raise

    await _record_success(source_updated, count)
    _set_progress(phase="done", ingested=count, total=count, error=None)
    log.info("scryfall.ingest.done", cards=count)
    return IngestResult(skipped=False, card_count=count, source_updated_at=source_updated)


async def _set_status(status: str) -> None:
    async with SessionLocal() as s:
        state = await s.get(IngestState, BULK_TYPE)
        if state is None:
            state = IngestState(bulk_type=BULK_TYPE)
            s.add(state)
        state.status = status
        await s.commit()


async def _record_success(source_updated: datetime.datetime | None, count: int) -> None:
    async with SessionLocal() as s:
        state = await s.get(IngestState, BULK_TYPE)
        if state is None:
            state = IngestState(bulk_type=BULK_TYPE)
            s.add(state)
        state.source_updated_at = source_updated
        state.last_downloaded_at = datetime.datetime.now(datetime.UTC)
        state.card_count = count
        state.status = "idle"
        await s.commit()


async def current_card_count() -> int:
    async with SessionLocal() as s:
        return await s.scalar(select(func.count()).select_from(Card)) or 0
