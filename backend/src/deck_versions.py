"""Deck versioning + diff (#100).

Snapshot a deck's card list at a point in time (a :class:`DeckVersion`, stored as JSONB so it's
immutable) and compare any two snapshots — or a snapshot and the deck's live state — to answer
"what changed since last week?". The diff is a simple line diff keyed by **card name + board**,
reporting what was added, removed, or changed in quantity, split by board.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import Deck, DeckVersion

BOARDS = [("main", "Mainboard"), ("side", "Sideboard")]


def snapshot_cards(deck: Deck) -> list[dict]:
    """Serialize a deck's current cards into a plain, storable snapshot list."""
    return [
        {"name": c.name, "quantity": c.quantity, "board": c.board,
         "oracle_id": str(c.oracle_id) if c.oracle_id else None,
         "scryfall_id": str(c.scryfall_id) if c.scryfall_id else None}
        for c in deck.cards
    ]


async def save_version(session: AsyncSession, deck: Deck, label: str = "") -> DeckVersion:
    """Persist a snapshot of the deck; auto-label ``v{n}`` when no label is given."""
    label = (label or "").strip()[:128]
    if not label:
        count = await session.scalar(
            select(func.count()).select_from(DeckVersion).where(DeckVersion.deck_id == deck.id)
        )
        label = f"v{(count or 0) + 1}"
    version = DeckVersion(deck_id=deck.id, label=label, cards=snapshot_cards(deck))
    session.add(version)
    await session.commit()
    await session.refresh(version)
    return version


async def list_versions(session: AsyncSession, deck_id: int) -> list[DeckVersion]:
    """A deck's saved versions, newest first."""
    return list((await session.execute(
        select(DeckVersion).where(DeckVersion.deck_id == deck_id)
        .order_by(DeckVersion.created_at.desc(), DeckVersion.id.desc())
    )).scalars().all())


@dataclass
class Change:
    name: str
    from_qty: int
    to_qty: int


@dataclass
class BoardDiff:
    board: str
    label: str
    added: list[Change] = field(default_factory=list)     # in B, not in A
    removed: list[Change] = field(default_factory=list)    # in A, not in B
    changed: list[Change] = field(default_factory=list)    # quantity differs
    unchanged: int = 0

    @property
    def has_changes(self) -> bool:
        return bool(self.added or self.removed or self.changed)


@dataclass
class DeckDiff:
    boards: list[BoardDiff] = field(default_factory=list)

    @property
    def has_changes(self) -> bool:
        return any(b.has_changes for b in self.boards)


def _totals_by_name(cards: list[dict], board: str) -> dict[str, tuple[str, int]]:
    """For one board: lowercased name -> (display name, summed quantity)."""
    out: dict[str, tuple[str, int]] = {}
    for c in cards:
        if (c.get("board") or "main") != board:
            continue
        name = c.get("name") or ""
        key = name.lower()
        display, qty = out.get(key, (name, 0))
        out[key] = (display, qty + int(c.get("quantity") or 0))
    return out


def diff_cards(a: list[dict], b: list[dict]) -> DeckDiff:
    """Diff two card lists (A → B), split by board, keyed by card name."""
    diff = DeckDiff()
    for board, label in BOARDS:
        at, bt = _totals_by_name(a, board), _totals_by_name(b, board)
        bd = BoardDiff(board=board, label=label)
        for key in sorted(at.keys() | bt.keys()):
            a_name, a_qty = at.get(key, (None, 0))
            b_name, b_qty = bt.get(key, (None, 0))
            name = b_name or a_name or ""
            if a_qty and not b_qty:
                bd.removed.append(Change(name, a_qty, 0))
            elif b_qty and not a_qty:
                bd.added.append(Change(name, 0, b_qty))
            elif a_qty != b_qty:
                bd.changed.append(Change(name, a_qty, b_qty))
            else:
                bd.unchanged += 1
        diff.boards.append(bd)
    return diff
