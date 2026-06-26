"""In-process scheduler for the periodic Scryfall bulk refresh.

Uses APScheduler so the app stays a single service (no Celery/Redis). The job is guarded by the
24h cache rule inside ``ingest_default_cards``, so a daily trigger never re-downloads early.
"""

from __future__ import annotations

import structlog
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from src.scryfall.ingest import ingest_default_cards

log = structlog.get_logger()

_scheduler: AsyncIOScheduler | None = None


async def _refresh_job() -> None:
    try:
        await ingest_default_cards()
        # Capture a price snapshot once prices are fresh (best-effort).
        from src.prices import take_snapshot

        await take_snapshot()
    except Exception as exc:  # noqa: BLE001 - never let a scheduled job crash the loop
        log.error("scryfall.refresh.failed", error=str(exc))


def start_scheduler(refresh_hours: int = 24) -> AsyncIOScheduler:
    global _scheduler
    if _scheduler is not None:
        return _scheduler
    _scheduler = AsyncIOScheduler()
    _scheduler.add_job(
        _refresh_job,
        IntervalTrigger(hours=refresh_hours),
        id="scryfall_refresh",
        replace_existing=True,
    )
    _scheduler.start()
    log.info("scheduler.started", refresh_hours=refresh_hours)
    return _scheduler


def shutdown_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
