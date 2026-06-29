"""Logical backup & restore of the user's data (everything except the reproducible card DB).

A backup is a single JSON document of the user tables — collection, decks, saved searches, price
history, wishlist, and the tags carried on the collection. The card database (`cards`,
`ingest_state`) is *not* included: it's rebuilt from Scryfall, so backups stay small and portable
and survive re-ingests.

Restore is a **replace**: the user tables are wiped and repopulated from the file (inside one
transaction). Rows that reference a card not present in the current database (e.g. restoring before
an ingest) are skipped and reported rather than failing the whole restore. Original primary keys
are preserved so deck/snapshot relationships stay intact; the identity sequences are then advanced.
"""

from __future__ import annotations

import datetime
import uuid
from dataclasses import dataclass, field

from sqlalchemy import Date, DateTime, insert, select, text
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import (
    Card,
    CardPricePoint,
    CollectionCard,
    Deck,
    DeckCard,
    PriceSnapshot,
    SavedSearch,
    WishlistItem,
)

BACKUP_VERSION = 1

# Parent-before-child order so preserved foreign keys insert cleanly on restore.
_TABLES = [
    ("collection_card", CollectionCard),
    ("saved_search", SavedSearch),
    ("wishlist", WishlistItem),
    ("deck", Deck),
    ("deck_card", DeckCard),
    ("price_snapshot", PriceSnapshot),
    ("card_price_point", CardPricePoint),
]
# Tables whose scryfall_id must exist in `cards` (FK); rows pointing at unknown cards are skipped.
_CARD_FK_TABLES = {"collection_card", "wishlist"}


def _to_json(value):
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, datetime.datetime | datetime.date):
        return value.isoformat()
    return value


def _from_json(column, value):
    if value is None:
        return None
    t = column.type
    if isinstance(t, PGUUID):
        return uuid.UUID(value)
    if isinstance(t, DateTime):
        return datetime.datetime.fromisoformat(value)
    if isinstance(t, Date):
        return datetime.date.fromisoformat(value)
    return value


@dataclass
class RestoreResult:
    ok: bool
    applied: bool = False
    error: str | None = None
    counts: dict[str, int] = field(default_factory=dict)
    skipped_missing_cards: int = 0

    @property
    def total(self) -> int:
        return sum(self.counts.values())


async def export_backup(session: AsyncSession) -> dict:
    """Serialize every user table to a JSON-able dict."""
    tables: dict[str, list] = {}
    for name, model in _TABLES:
        cols = [c.name for c in model.__table__.columns]
        rows = (await session.execute(select(model))).scalars().all()
        tables[name] = [{c: _to_json(getattr(r, c)) for c in cols} for r in rows]
    return {
        "version": BACKUP_VERSION,
        "exported_at": datetime.datetime.now(datetime.UTC).isoformat(),
        "tables": tables,
    }


def validate_backup(data) -> str | None:
    """Return an error message if *data* isn't a backup we can restore, else None."""
    if not isinstance(data, dict):
        return "That doesn't look like a scryme backup file."
    if data.get("version") != BACKUP_VERSION:
        return f"Unsupported backup version (this build restores version {BACKUP_VERSION})."
    if not isinstance(data.get("tables"), dict):
        return "The backup file is missing its data."
    return None


async def restore_backup(
    session: AsyncSession, data, *, dry_run: bool = True
) -> RestoreResult:
    """Validate and (unless dry_run) replace the user tables with the backup contents."""
    error = validate_backup(data)
    if error:
        return RestoreResult(ok=False, error=error)

    tables = data["tables"]
    existing_cards = set(await session.scalars(select(Card.scryfall_id)))

    prepared: dict[str, list[dict]] = {}
    counts: dict[str, int] = {}
    skipped = 0
    for name, model in _TABLES:
        cols = {c.name: c for c in model.__table__.columns}
        good: list[dict] = []
        for row in tables.get(name) or []:
            coerced = {k: _from_json(cols[k], v) for k, v in row.items() if k in cols}
            if name in _CARD_FK_TABLES and coerced.get("scryfall_id") not in existing_cards:
                skipped += 1
                continue
            good.append(coerced)
        prepared[name] = good
        counts[name] = len(good)

    if dry_run:
        return RestoreResult(ok=True, applied=False, counts=counts, skipped_missing_cards=skipped)

    await session.execute(
        text(
            "TRUNCATE collection_card, saved_search, wishlist, deck, deck_card, "
            "price_snapshot, card_price_point RESTART IDENTITY CASCADE"
        )
    )
    for name, model in _TABLES:
        if prepared[name]:
            await session.execute(insert(model.__table__), prepared[name])
    # Preserved explicit ids don't advance the identity sequences — bump them past the max.
    for name, _model in _TABLES:
        await session.execute(
            text(
                f"SELECT setval(pg_get_serial_sequence('{name}', 'id'), "
                f"GREATEST((SELECT COALESCE(MAX(id), 1) FROM {name}), 1))"
            )
        )
    await session.commit()
    return RestoreResult(ok=True, applied=True, counts=counts, skipped_missing_cards=skipped)
