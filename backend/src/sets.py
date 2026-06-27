"""Set completion: how much of each set the owned collection covers.

For every set the user owns at least one card from, compare the number of distinct printings
owned against the total printings scryme knows for that set (the paper cards in the ``cards``
table). The per-set drill-in lists the missing printings, naturally ordered by collector number.
"""

from __future__ import annotations

import datetime
import re
from dataclasses import dataclass

from sqlalchemy import distinct, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import Card, CollectionCard

_DIGITS = re.compile(r"\d+")


def _cn_key(collector_number: str | None) -> tuple[int, str]:
    """Natural sort key for collector numbers: leading number first, then the raw string.

    Scryfall numbers mix digits and suffixes (``1``, ``10``, ``12a``, ``★5``, ``T1``), so a plain
    string sort puts ``10`` before ``2``. Sort by the first embedded integer, then lexically.
    """
    cn = collector_number or ""
    m = _DIGITS.search(cn)
    return (int(m.group()) if m else 1 << 30, cn)


@dataclass
class SetProgress:
    code: str
    name: str
    set_type: str
    released_at: datetime.date | None
    total: int
    owned: int

    @property
    def missing(self) -> int:
        return self.total - self.owned

    @property
    def pct(self) -> float:
        return round(100 * self.owned / self.total, 1) if self.total else 0.0

    @property
    def complete(self) -> bool:
        return self.total > 0 and self.owned >= self.total


@dataclass
class MissingCard:
    scryfall_id: str
    name: str
    collector_number: str
    rarity: str | None


@dataclass
class SetDetail:
    code: str
    name: str
    set_type: str
    total: int
    owned: int
    missing_cards: list[MissingCard]

    @property
    def missing(self) -> int:
        return self.total - self.owned

    @property
    def pct(self) -> float:
        return round(100 * self.owned / self.total, 1) if self.total else 0.0


async def set_progress(session: AsyncSession) -> list[SetProgress]:
    """One row per set the collection owns from, most-complete first."""
    owned_rows = (
        await session.execute(
            select(Card.set_code, func.count(distinct(CollectionCard.scryfall_id)))
            .join(CollectionCard, CollectionCard.scryfall_id == Card.scryfall_id)
            .group_by(Card.set_code)
        )
    ).all()
    if not owned_rows:
        return []
    owned = {code: int(n) for code, n in owned_rows}

    total_rows = (
        await session.execute(
            select(
                Card.set_code,
                func.count(),
                func.max(Card.set_name),
                func.max(Card.raw["set_type"].astext),
                func.max(Card.released_at),
            )
            .where(Card.set_code.in_(list(owned)))
            .group_by(Card.set_code)
        )
    ).all()

    sets = [
        SetProgress(code=code, name=name or code.upper(), set_type=set_type or "",
                    released_at=released, total=int(total), owned=owned.get(code, 0))
        for code, total, name, set_type, released in total_rows
    ]
    # Most-complete first: the actionable end of the list (sets you're close to finishing), which
    # also sinks giant catch-all sets (Secret Lair, The List) you'll only ever own a sliver of.
    sets.sort(key=lambda s: (s.pct, s.owned, s.released_at or datetime.date.min), reverse=True)
    return sets


async def set_detail(session: AsyncSession, code: str) -> SetDetail | None:
    """Per-set breakdown with the missing printings, or None if the set is unknown."""
    code = code.lower()
    cards = (
        await session.execute(
            select(
                Card.scryfall_id, Card.name, Card.collector_number, Card.rarity,
                Card.set_name, Card.raw["set_type"].astext,
            ).where(func.lower(Card.set_code) == code)
        )
    ).all()
    if not cards:
        return None

    owned_ids = set(
        (
            await session.execute(
                select(distinct(CollectionCard.scryfall_id))
                .join(Card, Card.scryfall_id == CollectionCard.scryfall_id)
                .where(func.lower(Card.set_code) == code)
            )
        ).scalars()
    )

    missing = [
        MissingCard(scryfall_id=str(sid), name=name, collector_number=cn, rarity=rarity)
        for sid, name, cn, rarity, _set_name, _set_type in cards
        if sid not in owned_ids
    ]
    missing.sort(key=lambda m: _cn_key(m.collector_number))

    return SetDetail(
        code=code,
        name=cards[0].set_name or code.upper(),
        set_type=cards[0][5] or "",
        total=len(cards),
        owned=len(owned_ids),
        missing_cards=missing,
    )
