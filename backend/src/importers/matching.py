"""Resolve ImportRows to Scryfall cards.

Match priority: exact Scryfall ID → (set code, collector number) → card name → front face of a
multi-faced card. Rows that resolve to none are reported as unmatched. Lookups are batched so a
6000-row import issues a handful of queries, not thousands.

Name matching is **case-insensitive**, and falls back to the front face of split / double-faced
cards: Scryfall names those ``"Front // Back"``, but most exports write only ``"Front"``. This
mirrors ``src.decks._resolve_names``, which already did the same for decklists and check lists —
the two resolvers had drifted apart, so a DFC that imported fine from a decklist went unmatched
from a CSV.
"""

from __future__ import annotations

import datetime
import uuid
from dataclasses import dataclass

from sqlalchemy import func, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from src.importers.base import ImportRow
from src.models import Card

FACE_SEP = " // "


def _valid_uuid(value: str | None) -> bool:
    try:
        uuid.UUID(value)
        return True
    except (ValueError, TypeError, AttributeError):
        return False


def _is_playable(legalities: dict | None) -> bool:
    """True for a real, tournament-usable printing.

    Scryfall marks non-playable variants (art-series, tokens, gold-bordered World Championship /
    Collector's Edition, oversized, acorn un-cards) as ``not_legal`` in *every* format, so a
    printing counts as playable when it is legal/restricted/banned in at least one format. Art
    series cards are named ``"Name // Name"``, so without this the front-face fallback would
    happily resolve a plain name onto one.
    """
    return bool(legalities) and any(v != "not_legal" for v in legalities.values())


@dataclass
class MatchedRow:
    row: ImportRow
    scryfall_id: str | None
    method: str  # scryfall_id | set_number | name | name_front_face | unmatched

    @property
    def matched(self) -> bool:
        return self.scryfall_id is not None


async def _match_ids(session: AsyncSession, rows: list[ImportRow]) -> set[str]:
    # Only query syntactically-valid UUIDs; a malformed id falls through to set/number/name.
    ids = {r.scryfall_id for r in rows if _valid_uuid(r.scryfall_id)}
    if not ids:
        return set()
    res = await session.execute(select(Card.scryfall_id).where(Card.scryfall_id.in_(ids)))
    return {str(x) for (x,) in res.all()}


async def _match_pairs(session: AsyncSession, rows: list[ImportRow]) -> dict[tuple, str]:
    pairs = {(r.set_code, r.collector_number)
             for r in rows if r.set_code and r.collector_number}
    if not pairs:
        return {}
    res = await session.execute(
        select(Card.scryfall_id, Card.set_code, Card.collector_number).where(
            tuple_(Card.set_code, Card.collector_number).in_(list(pairs))
        )
    )
    return {(s, cn): str(sid) for sid, s, cn in res.all()}


async def _match_names(session: AsyncSession, rows: list[ImportRow]) -> dict[str, tuple[str, str]]:
    """Lowercased name -> (scryfall id, method), by exact name then by front face."""
    wanted = {r.name.lower() for r in rows if r.name}
    if not wanted:
        return {}

    # Ordered oldest-first so the last write per name is the most recent printing.
    res = await session.execute(
        select(Card.scryfall_id, func.lower(Card.name))
        .where(func.lower(Card.name).in_(wanted))
        .order_by(Card.released_at.asc().nullsfirst())
    )
    name_map = {name: (str(sid), "name") for sid, name in res.all()}

    unresolved = wanted - set(name_map)
    if unresolved:
        for name, sid in (await _match_front_faces(session, unresolved)).items():
            name_map[name] = (sid, "name_front_face")
    return name_map


async def _match_front_faces(session: AsyncSession, wanted: set[str]) -> dict[str, str]:
    """Resolve bare front-face names ("Delver of Secrets") to their full printing.

    One batched query over ``split_part(name, ' // ', 1)`` rather than a LIKE per name. Candidates
    are ranked playable-first, then newest, so a plain name never lands on an art-series variant.
    """
    front = func.lower(func.split_part(Card.name, FACE_SEP, 1))
    res = await session.execute(
        select(Card.scryfall_id, front, Card.legalities, Card.released_at)
        .where(front.in_(wanted), Card.name.contains(FACE_SEP))
    )
    best: dict[str, tuple] = {}
    for sid, name, legalities, released in res.all():
        rank = (_is_playable(legalities), released or datetime.date.min)
        if name not in best or rank > best[name][0]:
            best[name] = (rank, str(sid))
    return {name: sid for name, (_, sid) in best.items()}


async def match_rows(session: AsyncSession, rows: list[ImportRow]) -> list[MatchedRow]:
    existing_ids = await _match_ids(session, rows)
    pair_map = await _match_pairs(session, rows)
    name_map = await _match_names(session, rows)
    return [_match_one(r, existing_ids, pair_map, name_map) for r in rows]


def _match_one(
    r: ImportRow, existing_ids: set, pair_map: dict, name_map: dict
) -> MatchedRow:
    """Resolve one row by precedence: Scryfall id → set+number → name → front face → unmatched."""
    if r.scryfall_id and r.scryfall_id in existing_ids:
        return MatchedRow(r, r.scryfall_id, "scryfall_id")
    pair = (r.set_code, r.collector_number)
    if r.set_code and r.collector_number and pair in pair_map:
        return MatchedRow(r, pair_map[pair], "set_number")
    hit = name_map.get((r.name or "").lower())
    if hit is not None:
        sid, method = hit
        return MatchedRow(r, sid, method)
    return MatchedRow(r, None, "unmatched")
