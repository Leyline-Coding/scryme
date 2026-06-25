"""Scheduler tests: idempotent start/shutdown and crash-safe refresh job."""

import pytest
import src.scheduler as scheduler


@pytest.mark.asyncio
async def test_start_is_idempotent_then_shutdown():
    try:
        s1 = scheduler.start_scheduler(refresh_hours=24)
        s2 = scheduler.start_scheduler(refresh_hours=24)
        assert s1 is s2  # second start returns the same instance
        assert s1.get_job("scryfall_refresh") is not None
    finally:
        scheduler.shutdown_scheduler()
        scheduler.shutdown_scheduler()  # safe to call twice


@pytest.mark.asyncio
async def test_refresh_job_swallows_errors(monkeypatch):
    async def boom():
        raise RuntimeError("scryfall down")

    monkeypatch.setattr(scheduler, "ingest_default_cards", boom)
    # Must not raise — a failing scheduled job should never crash the loop.
    await scheduler._refresh_job()
