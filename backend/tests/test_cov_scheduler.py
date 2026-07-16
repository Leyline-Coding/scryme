"""Coverage tests for src/scheduler.py — job registration + the two job bodies.

The scheduler's jobs import their work lazily, so we patch each dependency on its source module.
We never let APScheduler actually fire a job; we invoke the job coroutines directly and inspect
the registered jobs' configuration.
"""

from __future__ import annotations

import pytest
import src.scheduler as scheduler


@pytest.fixture(autouse=True)
def _ensure_shutdown():
    yield
    scheduler.shutdown_scheduler()


@pytest.mark.asyncio
async def test_start_registers_backup_job_when_configured(monkeypatch, tmp_path):
    from src.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "backup_dir", tmp_path)
    monkeypatch.setattr(settings, "backup_interval_hours", 6)

    sched = scheduler.start_scheduler(refresh_hours=12)
    assert sched.get_job("scryfall_refresh") is not None
    assert sched.get_job("disk_backup") is not None
    # A second start is idempotent — returns the same running instance.
    assert scheduler.start_scheduler(refresh_hours=99) is sched


@pytest.mark.asyncio
async def test_start_skips_backup_job_without_dir(monkeypatch):
    from src.config import get_settings

    monkeypatch.setattr(get_settings(), "backup_dir", None)
    sched = scheduler.start_scheduler(refresh_hours=24)
    assert sched.get_job("disk_backup") is None


@pytest.mark.asyncio
async def test_refresh_job_success_runs_all_steps(monkeypatch):
    calls = []

    async def rec(name):
        calls.append(name)

    async def fake_ingest():
        calls.append("ingest")

    monkeypatch.setattr(scheduler, "ingest_default_cards", fake_ingest)
    monkeypatch.setattr("src.prices.take_snapshot", lambda: rec("snapshot"))
    monkeypatch.setattr("src.saved_alerts.evaluate_alerts", lambda: rec("alerts"))
    monkeypatch.setattr("src.price_watch.evaluate_targets", lambda: rec("targets"))
    monkeypatch.setattr("src.market_prices.sync_market_prices", lambda: rec("market"))

    await scheduler._refresh_job()
    assert calls == ["ingest", "snapshot", "alerts", "targets", "market"]


@pytest.mark.asyncio
async def test_refresh_job_swallows_errors(monkeypatch):
    async def boom():
        raise RuntimeError("scryfall down")

    monkeypatch.setattr(scheduler, "ingest_default_cards", boom)
    await scheduler._refresh_job()  # must not raise


@pytest.mark.asyncio
async def test_backup_job_no_dir_is_noop(monkeypatch):
    from src.config import get_settings

    monkeypatch.setattr(get_settings(), "backup_dir", None)
    # Should return before importing take_disk_backup; nothing to assert beyond no raise.
    await scheduler._backup_job()


@pytest.mark.asyncio
async def test_backup_job_writes(monkeypatch, tmp_path):
    from src.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "backup_dir", tmp_path)
    monkeypatch.setattr(settings, "backup_keep", 3)

    seen = {}

    async def fake_take(directory, keep=0, passphrase=""):
        seen["dir"] = directory
        seen["keep"] = keep
        return tmp_path / "scryme-backup-x.json"

    monkeypatch.setattr("src.backup.take_disk_backup", fake_take)
    await scheduler._backup_job()
    assert seen["dir"] == tmp_path and seen["keep"] == 3


@pytest.mark.asyncio
async def test_backup_job_swallows_errors(monkeypatch, tmp_path):
    from src.config import get_settings

    monkeypatch.setattr(get_settings(), "backup_dir", tmp_path)

    async def boom(*a, **k):
        raise RuntimeError("disk full")

    monkeypatch.setattr("src.backup.take_disk_backup", boom)
    await scheduler._backup_job()  # must not raise
