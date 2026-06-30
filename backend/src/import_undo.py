"""Undo last import (#59): snapshot the collection before a merge, restore it on demand.

A full serialized copy of ``collection_card`` is taken inside the same transaction that applies an
import, so undo atomically reverts replace / increment / per-card merges alike. Only the most recent
few snapshots are kept.
"""

from __future__ import annotations

import datetime
import uuid

import structlog
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import CollectionCard, ImportSnapshot

log = structlog.get_logger()

KEEP_SNAPSHOTS = 5


def _serialize(cc: CollectionCard) -> dict:
    return {
        "scryfall_id": str(cc.scryfall_id),
        "quantity": cc.quantity,
        "finish": cc.finish,
        "condition": cc.condition,
        "language": cc.language,
        "purchase_price": cc.purchase_price,
        "binder_name": cc.binder_name,
        "source_format": cc.source_format,
        "tags": cc.tags,
        "added_at": cc.added_at.isoformat() if cc.added_at else None,
    }


def _deserialize(row: dict) -> CollectionCard:
    added = row.get("added_at")
    return CollectionCard(
        scryfall_id=uuid.UUID(row["scryfall_id"]),
        quantity=row.get("quantity", 1),
        finish=row.get("finish", "normal"),
        condition=row.get("condition"),
        language=row.get("language", "en"),
        purchase_price=row.get("purchase_price"),
        binder_name=row.get("binder_name"),
        source_format=row.get("source_format"),
        tags=row.get("tags"),
        added_at=datetime.datetime.fromisoformat(added) if added else None,
    )


async def snapshot_collection(session: AsyncSession, label: str) -> ImportSnapshot:
    """Capture the current collection. Call inside the import transaction, before the merge."""
    rows = list((await session.execute(select(CollectionCard))).scalars())
    snap = ImportSnapshot(
        label=(label or "import")[:64],
        card_count=len(rows),
        payload=[_serialize(r) for r in rows],
    )
    session.add(snap)
    await session.flush()
    await _prune(session)
    return snap


async def _prune(session: AsyncSession) -> None:
    keep_ids = (
        await session.scalars(
            select(ImportSnapshot.id).order_by(ImportSnapshot.created_at.desc()).limit(KEEP_SNAPSHOTS)
        )
    ).all()
    if keep_ids:
        await session.execute(delete(ImportSnapshot).where(ImportSnapshot.id.not_in(keep_ids)))


async def latest_snapshot(session: AsyncSession) -> ImportSnapshot | None:
    return await session.scalar(
        select(ImportSnapshot).order_by(ImportSnapshot.created_at.desc()).limit(1)
    )


async def undo_last(session: AsyncSession) -> ImportSnapshot | None:
    """Restore the most recent snapshot (replacing the current collection) and consume it."""
    snap = await latest_snapshot(session)
    if snap is None:
        return None
    await session.execute(delete(CollectionCard))
    for row in snap.payload:
        session.add(_deserialize(row))
    await session.delete(snap)  # one-shot: the snapshot is consumed by the undo
    await session.commit()
    log.info("import_undo.restored", label=snap.label, cards=snap.card_count)
    return snap


async def snapshot_count(session: AsyncSession) -> int:
    return int(await session.scalar(select(func.count()).select_from(ImportSnapshot)) or 0)
