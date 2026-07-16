"""Resolve ImportRows to Scryfall cards.

Match priority: exact Scryfall ID → (set code, collector number) → card name (latest printing).
Rows that resolve to none are reported as unmatched. Lookups are batched so a 6000-row import
issues a handful of queries, not thousands.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from src.importers.base import ImportRow
from src.models import Card


def _valid_uuid(value: str | None) -> bool:
    try:
        uuid.UUID(value)
        return True
    except (ValueError, TypeError, AttributeError):
        return False


@dataclass
class MatchedRow:
    row: ImportRow
    scryfall_id: str | None
    method: str  # scryfall_id | set_number | name | unmatched

    @property
    def matched(self) -> bool:
        return self.scryfall_id is not None


async def match_rows(session: AsyncSession, rows: list[ImportRow]) -> list[MatchedRow]:
    # Only query syntactically-valid UUIDs; a malformed id falls through to set/number/name.
    ids = {r.scryfall_id for r in rows if _valid_uuid(r.scryfall_id)}
    existing_ids: set[str] = set()
    if ids:
        res = await session.execute(select(Card.scryfall_id).where(Card.scryfall_id.in_(ids)))
        existing_ids = {str(x) for (x,) in res.all()}

    pairs = {(r.set_code, r.collector_number)
             for r in rows if r.set_code and r.collector_number}
    pair_map: dict[tuple[str, str], str] = {}
    if pairs:
        res = await session.execute(
            select(Card.scryfall_id, Card.set_code, Card.collector_number).where(
                tuple_(Card.set_code, Card.collector_number).in_(list(pairs))
            )
        )
        for sid, s, cn in res.all():
            pair_map[(s, cn)] = str(sid)

    names = {r.name for r in rows}
    name_map: dict[str, str] = {}
    if names:
        # Ordered oldest-first so the last write per name is the most recent printing.
        res = await session.execute(
            select(Card.scryfall_id, Card.name)
            .where(Card.name.in_(names))
            .order_by(Card.released_at.asc().nullsfirst())
        )
        for sid, name in res.all():
            name_map[name] = str(sid)

    return [_match_one(r, existing_ids, pair_map, name_map) for r in rows]


def _match_one(
    r: ImportRow, existing_ids: set, pair_map: dict, name_map: dict
) -> MatchedRow:
    """Resolve one row by precedence: Scryfall id → set+number → name → unmatched."""
    if r.scryfall_id and r.scryfall_id in existing_ids:
        return MatchedRow(r, r.scryfall_id, "scryfall_id")
    pair = (r.set_code, r.collector_number)
    if r.set_code and r.collector_number and pair in pair_map:
        return MatchedRow(r, pair_map[pair], "set_number")
    if r.name in name_map:
        return MatchedRow(r, name_map[r.name], "name")
    return MatchedRow(r, None, "unmatched")
