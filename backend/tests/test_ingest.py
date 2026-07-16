"""Ingestion tests: streaming parse + upsert, idempotency, and the 24h refresh guard."""

import datetime
import gzip
import shutil
from pathlib import Path

import pytest
from sqlalchemy import func, select
from src.models import Card, IngestState
from src.scryfall.ingest import (
    _guard_allows_refresh,
    ingest_default_cards,
    ingest_from_path,
)

FIXTURES = Path(__file__).parent / "fixtures"
SAMPLE = FIXTURES / "scryfall_sample.json"


@pytest.mark.asyncio
async def test_ingest_from_plain_json(session):
    count = await ingest_from_path(SAMPLE)
    assert count == 3
    total = await session.scalar(select(func.count()).select_from(Card))
    assert total == 3
    bolt = await session.scalar(select(Card).where(Card.name == "Lightning Bolt"))
    assert bolt.set_code == "mh2"
    assert bolt.colors == ["R"]


@pytest.mark.asyncio
async def test_ingest_from_gzip(session, tmp_path):
    gz = tmp_path / "cards.json.gz"
    with gzip.open(gz, "wb") as fh:
        fh.write(SAMPLE.read_bytes())
    count = await ingest_from_path(gz)
    assert count == 3


@pytest.mark.asyncio
async def test_ingest_is_idempotent(session):
    await ingest_from_path(SAMPLE)
    await ingest_from_path(SAMPLE)  # re-ingest must upsert, not duplicate
    total = await session.scalar(select(func.count()).select_from(Card))
    assert total == 3


@pytest.mark.asyncio
async def test_reingest_preserves_image_status(session):
    await ingest_from_path(SAMPLE)
    lotus = await session.scalar(select(Card).where(Card.name == "Black Lotus"))
    lotus.image_status = "cached"
    await session.commit()

    await ingest_from_path(SAMPLE)
    lotus2 = await session.scalar(select(Card).where(Card.name == "Black Lotus"))
    assert lotus2.image_status == "cached"  # not reset to 'pending'


class FakeScryfallClient:
    """Stands in for ScryfallClient: serves the fixture as the bulk file."""

    def __init__(self, updated_at: str):
        self._updated_at = updated_at

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get_bulk_entry(self, bulk_type="default_cards"):
        return {
            "type": bulk_type,
            "updated_at": self._updated_at,
            "download_uri": "https://example.test/default-cards.json",
        }

    async def download_to_file(self, url, dest: Path):
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(SAMPLE, dest)
        return dest


@pytest.mark.asyncio
async def test_ingest_default_cards_end_to_end(session):
    client = FakeScryfallClient(updated_at="2026-06-25T09:00:00+00:00")
    result = await ingest_default_cards(client=client, force=True)
    assert result.skipped is False
    assert result.card_count == 3

    state = await session.get(IngestState, "default_cards")
    assert state.status == "idle"
    assert state.card_count == 3
    assert state.last_downloaded_at is not None


@pytest.mark.asyncio
async def test_ingest_default_cards_respects_cache_guard(session):
    client = FakeScryfallClient(updated_at="2026-06-25T09:00:00+00:00")
    await ingest_default_cards(client=client, force=True)
    # Second call without force, same bulk, within 24h -> skipped.
    result = await ingest_default_cards(client=client, force=False)
    assert result.skipped is True
    assert result.reason == "cached"


@pytest.mark.asyncio
async def test_guard_allows_refresh_rules():
    now = datetime.datetime.now(datetime.UTC)
    src = now
    # No state -> always allowed.
    assert _guard_allows_refresh(None, src, 24) is True

    fresh = IngestState(
        bulk_type="default_cards",
        source_updated_at=src,
        last_downloaded_at=now,
        card_count=3,
    )
    # Same bulk, just downloaded -> blocked.
    assert _guard_allows_refresh(fresh, src, 24) is False
    # Newer bulk available -> allowed even within the window.
    assert _guard_allows_refresh(fresh, now + datetime.timedelta(hours=1), 24) is True
    # Old download -> allowed.
    stale = IngestState(
        bulk_type="default_cards",
        source_updated_at=src,
        last_downloaded_at=now - datetime.timedelta(hours=30),
        card_count=3,
    )
    assert _guard_allows_refresh(stale, src, 24) is True
