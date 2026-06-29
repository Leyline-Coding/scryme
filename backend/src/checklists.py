"""Custom checklists: build a named card list, then track which entries you own.

Reuses the deck list parser + name resolver. Each line becomes one item (quantities ignored,
duplicates collapsed); ownership is matched by oracle id so any printing you own counts.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.decks import _owned_by_oracle, _resolve_names
from src.models import Checklist, ChecklistItem, CollectionCard
from src.wishlist import add_to_wishlist

# A checklist line is a bare card name, optionally with a leading count and a trailing printing
# hint / foil marker (so decklists paste in too).
_QTY = re.compile(r"^\s*\d+\s*[xX]?\s+")
_MARKER = re.compile(r"(\s*\*[^*]*\*)+\s*$")
_SET_SUFFIX = re.compile(r"\s*\([A-Za-z0-9]{2,6}\)\s*[A-Za-z0-9-]*\s*$")


def _distinct_names(text: str | None) -> list[str]:
    seen: set[str] = set()
    names: list[str] = []
    for raw in (text or "").splitlines():
        s = raw.strip()
        if not s or s.startswith(("#", "//")) or s.lower().startswith("sideboard"):
            continue
        s = _QTY.sub("", s)
        s = _MARKER.sub("", s)
        s = _SET_SUFFIX.sub("", s).strip()
        if not s:
            continue
        key = s.lower()
        if key not in seen:
            seen.add(key)
            names.append(s)
    return names


async def create_checklist(session: AsyncSession, name: str, text: str) -> Checklist:
    names = _distinct_names(text)
    owned_sids = set(await session.scalars(select(CollectionCard.scryfall_id)))
    resolved = await _resolve_names(session, names, owned_sids)

    checklist = Checklist(name=(name or "").strip()[:256] or "Untitled checklist")
    for n in names:
        oracle, sid = resolved.get(n.lower(), (None, None))
        checklist.items.append(ChecklistItem(name=n, oracle_id=oracle, scryfall_id=sid))
    session.add(checklist)
    await session.commit()
    await session.refresh(checklist)
    return checklist


@dataclass
class ChecklistRow:
    name: str
    scryfall_id: str | None
    matched: bool
    owned: bool


@dataclass
class ChecklistCoverage:
    checklist: Checklist
    rows: list[ChecklistRow] = field(default_factory=list)
    total: int = 0
    owned_count: int = 0
    unmatched: int = 0

    @property
    def missing(self) -> list[ChecklistRow]:
        return [r for r in self.rows if not r.owned]

    @property
    def missing_matched(self) -> int:
        return sum(1 for r in self.rows if not r.owned and r.matched)

    @property
    def pct_complete(self) -> int:
        return round(100 * self.owned_count / self.total) if self.total else 0


async def checklist_coverage(session: AsyncSession, checklist: Checklist) -> ChecklistCoverage:
    owned = await _owned_by_oracle(session)
    cov = ChecklistCoverage(checklist=checklist, total=len(checklist.items))
    for item in checklist.items:
        matched = item.oracle_id is not None
        is_owned = bool(matched and owned.get(item.oracle_id, 0) > 0)
        if not matched:
            cov.unmatched += 1
        if is_owned:
            cov.owned_count += 1
        cov.rows.append(
            ChecklistRow(
                name=item.name,
                scryfall_id=str(item.scryfall_id) if item.scryfall_id else None,
                matched=matched, owned=is_owned,
            )
        )
    return cov


async def add_checklist_missing(session: AsyncSession, checklist: Checklist) -> int:
    """Add every missing (matched) checklist card to the wishlist. Returns how many were added."""
    cov = await checklist_coverage(session, checklist)
    added = 0
    for row in cov.missing:
        if row.scryfall_id:
            await add_to_wishlist(session, row.scryfall_id, 1, note=f"checklist: {checklist.name}")
            added += 1
    return added
