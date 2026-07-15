"""In-process scheduler for the periodic Scryfall bulk refresh.

Uses APScheduler so the app stays a single service (no Celery/Redis). The job is guarded by the
24h cache rule inside ``ingest_default_cards``, so a daily trigger never re-downloads early.
"""

from __future__ import annotations

import structlog
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from src.config import get_settings
from src.scryfall.ingest import ingest_default_cards

log = structlog.get_logger()

_scheduler: AsyncIOScheduler | None = None


async def _refresh_job() -> None:
    try:
        await ingest_default_cards()
        # Capture a price snapshot once prices are fresh (best-effort).
        from src.prices import take_snapshot

        await take_snapshot()
        # Re-evaluate saved searches against the new data and record newly-matching cards (#58).
        from src.saved_alerts import evaluate_alerts

        await evaluate_alerts()
        # Re-evaluate price-watchlist targets against the refreshed prices (#88).
        from src.price_watch import evaluate_targets

        await evaluate_targets()
        # Refresh alternate-marketplace prices (Card Kingdom + ManaPool, #231) — best-effort, each
        # source guarded by its own daily cache and isolated so one failure doesn't stop the others.
        from src.market_prices import sync_market_prices

        await sync_market_prices()
    except Exception as exc:  # noqa: BLE001 - never let a scheduled job crash the loop
        log.error("scryfall.refresh.failed", error=str(exc))


async def _backup_job() -> None:
    settings = get_settings()
    if not settings.backup_dir:
        return
    try:
        from src.backup import take_disk_backup

        path = await take_disk_backup(settings.backup_dir, keep=settings.backup_keep,
                                      passphrase=settings.backup_passphrase)
        log.info("backup.scheduled.done", path=str(path))
    except Exception as exc:  # noqa: BLE001 - never let a scheduled job crash the loop
        log.error("backup.scheduled.failed", error=str(exc))


def start_scheduler(refresh_hours: int = 24) -> AsyncIOScheduler:
    global _scheduler
    if _scheduler is not None:
        return _scheduler
    settings = get_settings()
    _scheduler = AsyncIOScheduler()
    _scheduler.add_job(
        _refresh_job,
        IntervalTrigger(hours=refresh_hours),
        id="scryfall_refresh",
        replace_existing=True,
    )
    # Scheduled on-disk backups are opt-in: a backup directory + a positive interval.
    if settings.backup_dir and settings.backup_interval_hours > 0:
        _scheduler.add_job(
            _backup_job,
            IntervalTrigger(hours=settings.backup_interval_hours),
            id="disk_backup",
            replace_existing=True,
        )
        log.info("scheduler.backup_enabled", hours=settings.backup_interval_hours,
                 dir=str(settings.backup_dir))
    _scheduler.start()
    log.info("scheduler.started", refresh_hours=refresh_hours)
    return _scheduler


def shutdown_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
