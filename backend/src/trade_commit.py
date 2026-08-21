"""Settling a trade: move the staged cards in and out of the collection, and say what happened.

The shape of this module is dictated by :doc:`ADR 0002 </development/adr/0002-cross-device-trading>`
(D3), which asks for one specific operation: *apply this set of in/out moves atomically at the card
level, reporting exactly which ones applied*. That is more than a purely local trade needs, and
it's deliberate — a trade's two halves will eventually be committed on two different machines, and
"atomic" cannot mean all-or-nothing at the trade level when
`#332 <https://github.com/Leyline-Coding/scryme/issues/332>`_ explicitly asks for a trade that
falls through on some cards mid-session to be representable.

So:

* **Atomic per card, not per trade.** Every line either moves the number of copies it says, or
  moves fewer and reports the shortfall. One line failing never rolls back the ones that worked.
* **One transaction.** Nothing is committed until every line has been processed, so a crash
  halfway leaves the collection exactly as it was — a half-applied trade is worse than a failed one.
* **What's left staged is what didn't happen.** Applying consumes ``applied_quantity``; the
  outstanding remainder stays in the pool, visible, for the parties to sort out.
* **Outgoing cards are re-resolved, never addressed by row id.** A staged item names a *copy kind*
  (printing + finish + condition + language) and the commit finds the stacks matching it now. The
  originating stack may have been merged, split or deleted since staging (D4).
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.collection_edit import add_or_increment
from src.models import CollectionCard, TradePool, TradePoolItem
from src.models.trade_pool import CLOSED, OUT

# Per-line outcomes, in the order a reviewer cares about them.
APPLIED = "applied"
PARTIAL = "partial"    # some copies moved, fewer than agreed
FAILED = "failed"      # nothing moved — none of that copy kind is in the collection


@dataclass
class LineResult:
    item_id: int
    direction: str
    name: str
    requested: int          # copies still outstanding when the commit started
    applied: int
    status: str

    @property
    def short(self) -> int:
        return self.requested - self.applied


@dataclass
class TradeResult:
    lines: list[LineResult] = field(default_factory=list)
    completed: bool = False   # every line in the whole pool is now fully applied

    @property
    def moved_out(self) -> int:
        return sum(r.applied for r in self.lines if r.direction == OUT)

    @property
    def moved_in(self) -> int:
        return sum(r.applied for r in self.lines if r.direction != OUT)

    @property
    def problems(self) -> list[LineResult]:
        return [r for r in self.lines if r.status != APPLIED]

    @property
    def ok(self) -> bool:
        return bool(self.lines) and not self.problems


def _eq_or_null(col, value):
    return col.is_(None) if value is None else col == value


async def _remove_copies(session: AsyncSession, item: TradePoolItem, wanted: int) -> int:
    """Take up to ``wanted`` copies of this item's copy kind out of the collection.

    Re-resolved by copy kind rather than by the stack the card was staged from (ADR 0002 D4), and
    drawn from the smallest matching stack first so an odd single copy filed somewhere is used up
    before a bigger stack is broken into.
    """
    stacks = list(
        (
            await session.execute(
                select(CollectionCard).where(
                    CollectionCard.scryfall_id == item.scryfall_id,
                    CollectionCard.finish == item.finish,
                    CollectionCard.language == item.language,
                    _eq_or_null(CollectionCard.condition, item.condition),
                ).order_by(CollectionCard.quantity.asc())
            )
        ).scalars().all()
    )
    taken = 0
    for stack in stacks:
        if taken >= wanted:
            break
        take = min(stack.quantity, wanted - taken)
        stack.quantity -= take
        taken += take
        if stack.quantity <= 0:
            await session.delete(stack)
    return taken


async def _apply_line(
    session: AsyncSession, item: TradePoolItem, *, binder: str | None, location: str | None
) -> LineResult:
    outstanding = max(0, item.quantity - item.applied_quantity)
    name = item.card.name

    if item.direction == OUT:
        applied = await _remove_copies(session, item, outstanding)
    else:
        stack = await add_or_increment(
            session, item.scryfall_id, outstanding,
            finish=item.finish, condition=item.condition, language=item.language,
            binder=binder, location=location, commit=False,
        )
        # None only if the printing vanished from the card DB between staging and committing.
        applied = outstanding if stack is not None else 0

    item.applied_quantity += applied
    if applied == outstanding:
        status = APPLIED
    elif applied:
        status = PARTIAL
    else:
        status = FAILED
    return LineResult(item_id=item.id, direction=item.direction, name=name,
                      requested=outstanding, applied=applied, status=status)


async def outstanding_items(session: AsyncSession, pool_id: int) -> list[TradePoolItem]:
    """Staged lines with copies still to move — what a commit would act on."""
    items = list(
        (
            await session.execute(
                select(TradePoolItem).where(TradePoolItem.pool_id == pool_id)
                .order_by(TradePoolItem.direction, TradePoolItem.id)
            )
        ).scalars().all()
    )
    return [i for i in items if i.quantity > i.applied_quantity]


async def commit_trade(
    session: AsyncSession,
    pool_id: int,
    item_ids: list[int] | None = None,
    *,
    binder: str | None = None,
    location: str | None = None,
) -> TradeResult | None:
    """Reconcile a pool (or the chosen lines of it) with the collection. None if no such pool.

    ``item_ids`` is how a trade that falls through on some cards is committed: settle the lines
    that held, leave the rest staged. ``binder`` / ``location`` file the incoming cards, which is
    the same intake a manual addition gets.
    """
    pool = await session.get(TradePool, pool_id)
    if pool is None:
        return None

    pending = await outstanding_items(session, pool_id)
    chosen = [i for i in pending if item_ids is None or i.id in set(item_ids)]

    result = TradeResult()
    for item in chosen:
        result.lines.append(
            await _apply_line(session, item, binder=binder, location=location)
        )

    # Re-read after the writes: a line can be fully applied now that wasn't when we started.
    still_open = [i for i in pending if i.quantity > i.applied_quantity]
    result.completed = bool(pending) and not still_open
    if result.completed:
        pool.status = CLOSED
        pool.committed_at = datetime.datetime.now(datetime.UTC)

    # One commit for the whole trade — see the module docstring.
    await session.commit()
    return result
