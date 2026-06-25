"""Apply matched import rows to the collection under a chosen merge strategy.

A "stack" is a distinct (card, finish, condition, language, binder) combination — the same unique
key as ``collection_card``. Existing stacks are loaded into memory (the single-user collection is
bounded) so NULL-bearing keys compare correctly without awkward SQL.

Strategies:
  * REPLACE   — wipe the collection, then insert the import's stacks.
  * INCREMENT — add the import's quantities on top of what's already owned.
  * PER_CARD  — for each stack that already exists, follow a per-conflict decision
                ("increment" or "replace"); brand-new stacks are always inserted.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.importers.matching import MatchedRow
from src.models import CollectionCard

StackKey = tuple[str, str, str | None, str, str | None]  # (sid, finish, condition, lang, binder)


class MergeStrategy(str, Enum):
    REPLACE = "replace"
    INCREMENT = "increment"
    PER_CARD = "per_card"


@dataclass
class Stack:
    scryfall_id: str
    finish: str
    condition: str | None
    language: str
    binder_name: str | None
    quantity: int
    purchase_price: float | None
    name: str  # for display only

    @property
    def key(self) -> StackKey:
        return (self.scryfall_id, self.finish, self.condition, self.language, self.binder_name)


@dataclass
class Conflict:
    index: int
    name: str
    finish: str
    existing_qty: int
    import_qty: int


def aggregate(matched: list[MatchedRow]) -> dict[StackKey, Stack]:
    """Collapse matched rows into stacks, summing quantities of identical stacks."""
    stacks: dict[StackKey, Stack] = {}
    for m in matched:
        if not m.scryfall_id:
            continue
        r = m.row
        stack = Stack(
            scryfall_id=m.scryfall_id,
            finish=r.finish,
            condition=r.condition,
            language=r.language,
            binder_name=r.binder_name,
            quantity=r.quantity,
            purchase_price=r.purchase_price,
            name=r.name,
        )
        if stack.key in stacks:
            stacks[stack.key].quantity += r.quantity
        else:
            stacks[stack.key] = stack
    return stacks


async def load_existing(session: AsyncSession) -> dict[StackKey, CollectionCard]:
    res = await session.execute(select(CollectionCard))
    return {
        (str(c.scryfall_id), c.finish, c.condition, c.language, c.binder_name): c
        for c in res.scalars().all()
    }


def find_conflicts(
    existing: dict[StackKey, CollectionCard], stacks: dict[StackKey, Stack]
) -> list[Conflict]:
    """Stacks present in both the import and the current collection, deterministically ordered."""
    keys = sorted(stacks.keys() & existing.keys(), key=lambda k: stacks[k].name.lower())
    return [
        Conflict(
            index=i,
            name=stacks[k].name,
            finish=stacks[k].finish,
            existing_qty=existing[k].quantity,
            import_qty=stacks[k].quantity,
        )
        for i, k in enumerate(keys)
    ]


@dataclass
class MergeSummary:
    strategy: MergeStrategy
    inserted: int
    updated: int
    total_quantity: int


def _insert(session: AsyncSession, stack: Stack, source_format: str | None) -> None:
    session.add(
        CollectionCard(
            scryfall_id=stack.scryfall_id,
            quantity=stack.quantity,
            finish=stack.finish,
            condition=stack.condition,
            language=stack.language,
            purchase_price=stack.purchase_price,
            binder_name=stack.binder_name,
            source_format=source_format,
        )
    )


async def apply_merge(
    session: AsyncSession,
    matched: list[MatchedRow],
    strategy: MergeStrategy,
    *,
    decisions: dict[int, str] | None = None,
    source_format: str | None = None,
) -> MergeSummary:
    decisions = decisions or {}
    stacks = aggregate(matched)

    if strategy is MergeStrategy.REPLACE:
        await session.execute(delete(CollectionCard))
        for stack in stacks.values():
            _insert(session, stack, source_format)
        await session.commit()
        return MergeSummary(strategy, inserted=len(stacks), updated=0,
                            total_quantity=sum(s.quantity for s in stacks.values()))

    existing = await load_existing(session)
    conflict_keys = [k for k in sorted(stacks.keys() & existing.keys(),
                                       key=lambda k: stacks[k].name.lower())]
    decision_for = {conflict_keys[i]: choice for i, choice in decisions.items()
                    if i < len(conflict_keys)}

    inserted = updated = 0
    for key, stack in stacks.items():
        if key in existing:
            choice = "increment"
            if strategy is MergeStrategy.PER_CARD:
                choice = decision_for.get(key, "increment")
            if choice == "replace":
                existing[key].quantity = stack.quantity
            else:
                existing[key].quantity += stack.quantity
            existing[key].source_format = source_format
            updated += 1
        else:
            _insert(session, stack, source_format)
            inserted += 1

    await session.commit()
    total = await session.scalar(select(func.coalesce(func.sum(CollectionCard.quantity), 0)))
    return MergeSummary(
        strategy, inserted=inserted, updated=updated, total_quantity=int(total or 0)
    )
