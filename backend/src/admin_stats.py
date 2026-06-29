"""Operational stats for the admin dashboard and the Prometheus metrics endpoint.

One cheap pass of count/aggregate queries (the collection is small) plus a couple of Postgres
introspection calls. Image-cache *disk size* is computed separately (a directory walk) and only
for the human dashboard — never on a metrics scrape.
"""

from __future__ import annotations

import datetime
import os
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from src import __version__
from src.config import get_settings
from src.models import (
    Card,
    Checklist,
    CollectionCard,
    Deck,
    IngestState,
    PriceSnapshot,
    SavedSearch,
    WishlistItem,
)
from src.scryfall.ingest import BULK_TYPE


@dataclass
class AdminStats:
    version: str
    read_only: bool
    card_count: int
    images_cached: int
    ingest_status: str
    last_ingest: datetime.datetime | None
    source_updated_at: datetime.datetime | None
    collection_cards: int
    collection_printings: int
    decks: int
    wishlist: int
    checklists: int
    saved_searches: int
    last_snapshot_at: datetime.datetime | None
    last_snapshot_total: float
    db_size_bytes: int
    backup_dir: str | None
    backup_interval_hours: int


async def _count(session: AsyncSession, model) -> int:
    return await session.scalar(select(func.count()).select_from(model)) or 0


async def collect_admin_stats(session: AsyncSession) -> AdminStats:
    settings = get_settings()
    state = await session.get(IngestState, BULK_TYPE)

    images_cached = await session.scalar(
        select(func.count()).select_from(Card).where(Card.image_status == "cached")
    ) or 0
    coll = (
        await session.execute(
            select(
                func.coalesce(func.sum(CollectionCard.quantity), 0),
                func.count(func.distinct(CollectionCard.scryfall_id)),
            )
        )
    ).one()
    snap = (
        await session.execute(
            select(PriceSnapshot.captured_at, PriceSnapshot.total_usd)
            .order_by(PriceSnapshot.captured_at.desc())
            .limit(1)
        )
    ).first()
    db_size = await session.scalar(text("SELECT pg_database_size(current_database())")) or 0

    return AdminStats(
        version=__version__,
        read_only=settings.read_only,
        card_count=await _count(session, Card),
        images_cached=int(images_cached),
        ingest_status=state.status if state else "never",
        last_ingest=state.last_downloaded_at if state else None,
        source_updated_at=state.source_updated_at if state else None,
        collection_cards=int(coll[0]),
        collection_printings=int(coll[1]),
        decks=await _count(session, Deck),
        wishlist=await _count(session, WishlistItem),
        checklists=await _count(session, Checklist),
        saved_searches=await _count(session, SavedSearch),
        last_snapshot_at=snap[0] if snap else None,
        last_snapshot_total=float(snap[1]) if snap else 0.0,
        db_size_bytes=int(db_size),
        backup_dir=str(settings.backup_dir) if settings.backup_dir else None,
        backup_interval_hours=settings.backup_interval_hours,
    )


def image_cache_disk(directory: Path) -> tuple[int, int]:
    """(file count, total bytes) under *directory* — a walk, for the dashboard only."""
    if not directory or not directory.is_dir():
        return 0, 0
    count = 0
    total = 0
    for root, _dirs, files in os.walk(directory):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(root, name))
                count += 1
            except OSError:
                continue
    return count, total


def render_metrics(stats: AdminStats) -> str:
    """Prometheus text exposition of the operational gauges."""
    def metric(name: str, value, help_text: str) -> str:
        return f"# HELP {name} {help_text}\n# TYPE {name} gauge\n{name} {value}\n"

    last_ingest = int(stats.last_ingest.timestamp()) if stats.last_ingest else 0
    last_snap = int(stats.last_snapshot_at.timestamp()) if stats.last_snapshot_at else 0
    parts = [
        metric("scryme_cards_total", stats.card_count, "Cards in the local database"),
        metric("scryme_images_cached_total", stats.images_cached, "Cards with a cached image"),
        metric("scryme_collection_cards_total", stats.collection_cards,
               "Total owned cards (sum of quantities)"),
        metric("scryme_collection_printings_total", stats.collection_printings,
               "Distinct owned printings"),
        metric("scryme_decks_total", stats.decks, "Saved decks"),
        metric("scryme_wishlist_total", stats.wishlist, "Wishlist entries"),
        metric("scryme_checklists_total", stats.checklists, "Custom checklists"),
        metric("scryme_saved_searches_total", stats.saved_searches, "Saved searches"),
        metric("scryme_collection_value_usd", round(stats.last_snapshot_total, 2),
               "Collection value (USD) at the last price snapshot"),
        metric("scryme_last_ingest_timestamp_seconds", last_ingest,
               "Unix time of the last Scryfall ingest (0 = never)"),
        metric("scryme_last_snapshot_timestamp_seconds", last_snap,
               "Unix time of the last price snapshot (0 = never)"),
        metric("scryme_db_size_bytes", stats.db_size_bytes, "PostgreSQL database size in bytes"),
        f'# HELP scryme_info Build info\n# TYPE scryme_info gauge\n'
        f'scryme_info{{version="{stats.version}"}} 1\n',
    ]
    return "".join(parts)
