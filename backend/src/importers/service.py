"""Orchestrate the two-phase import: stage a preview, then confirm a merge.

``stage_upload`` parses + matches the file, persists the matched rows to ``import_staging``, and
returns a preview (counts, conflicts, unmatched samples). ``confirm_upload`` loads the staged
rows, applies the chosen strategy, and clears the staging row.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from src.importers.base import ImportRow, UnknownFormatError, detect_format
from src.importers.mapping import parse_with_mapping
from src.importers.matching import MatchedRow, match_rows
from src.importers.merge import (
    Conflict,
    MergeStrategy,
    MergeSummary,
    aggregate,
    apply_merge,
    find_conflicts,
    load_existing,
)
from src.models import ImportStaging

PREVIEW_SAMPLE = 25


@dataclass
class ImportPreview:
    token: str
    source_format: str
    total_rows: int
    matched_count: int
    unmatched_count: int
    new_stacks: int
    unmatched_samples: list[ImportRow]
    conflicts: list[Conflict]


async def _stage_rows(
    session: AsyncSession, rows: list[ImportRow], source_format: str
) -> ImportPreview:
    """Match rows, persist the staging row, and build the preview (shared by both entry points)."""
    matched = await match_rows(session, rows)

    staging = ImportStaging(
        source_format=source_format,
        payload=[
            {"row": m.row.to_dict(), "scryfall_id": m.scryfall_id, "method": m.method}
            for m in matched
        ],
    )
    session.add(staging)
    await session.commit()

    stacks = aggregate(matched)
    existing = await load_existing(session)
    conflicts = find_conflicts(existing, stacks)
    unmatched = [m.row for m in matched if not m.matched]

    return ImportPreview(
        token=str(staging.token),
        source_format=source_format,
        total_rows=len(rows),
        matched_count=sum(1 for m in matched if m.matched),
        unmatched_count=len(unmatched),
        new_stacks=len(stacks) - len(conflicts),
        unmatched_samples=unmatched[:PREVIEW_SAMPLE],
        conflicts=conflicts,
    )


async def stage_upload(session: AsyncSession, text: str) -> ImportPreview:
    importer = detect_format(text)
    if importer is None:
        raise UnknownFormatError(
            "Unrecognized file. Expected a ManaBox, Dragon Shield, or Delver Lens export."
        )
    return await _stage_rows(session, importer.parse(text), importer.format_name)


async def stage_mapped_upload(
    session: AsyncSession, text: str, mapping: dict[str, str]
) -> ImportPreview:
    """Stage a CSV parsed via a user-supplied column mapping (the import wizard)."""
    rows = parse_with_mapping(text, mapping)
    if not rows:
        raise UnknownFormatError("No rows parsed — check that the Card name column is mapped.")
    return await _stage_rows(session, rows, "mapped")


async def _load_staged(session: AsyncSession, token: str) -> tuple[ImportStaging, list[MatchedRow]]:
    try:
        staging = await session.get(ImportStaging, uuid.UUID(token))
    except (ValueError, TypeError):
        staging = None
    if staging is None:
        raise UnknownFormatError("This import has expired or was already applied.")
    matched = [
        MatchedRow(ImportRow.from_dict(p["row"]), p["scryfall_id"], p["method"])
        for p in staging.payload
    ]
    return staging, matched


async def confirm_upload(
    session: AsyncSession,
    token: str,
    strategy: MergeStrategy,
    decisions: dict[int, str] | None = None,
) -> MergeSummary:
    staging, matched = await _load_staged(session, token)
    # Snapshot the collection before the merge so a bad import can be undone (#59). Part of the same
    # transaction, so the snapshot and the merge commit (or roll back) together.
    from src.import_undo import snapshot_collection

    await snapshot_collection(session, f"{staging.source_format} import")
    summary = await apply_merge(
        session, matched, strategy, decisions=decisions, source_format=staging.source_format
    )
    await session.delete(staging)
    await session.commit()
    return summary
