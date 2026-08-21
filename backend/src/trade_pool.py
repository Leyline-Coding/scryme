"""Staging a trade: pick specific copies to give and to get, then read the two sides off (#331).

The surplus binder in :mod:`src.trade` answers "what *could* I trade?". A pool answers "what am I
trading, with this person, right now?" — a chosen list of specific printings in specific finishes
and conditions, persisted until it's committed or cleared.

Two rules from :doc:`ADR 0002 </development/adr/0002-cross-device-trading>` shape the API here:

* **Nothing addresses a collection row.** Staging an owned stack copies its value-bearing
  attributes onto the item and keeps the stack id only as a breadcrumb. Everything downstream —
  the owned/short check here, and the commit in #332 — re-resolves by
  ``(scryfall_id, finish, condition, language)``. A pool therefore survives a stack being merged,
  re-graded or deleted, and can be handed to another device that has no idea what our row ids
  mean (D1, D4).
* **One valuation basis per pool.** Values are computed with the pool's own ``currency`` and
  ``price_source``, not the viewer's, so the totals don't move depending on who's looking (D2).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.currency import unit_price
from src.models import Card, CollectionCard, TradePool, TradePoolItem
from src.models.trade_pool import CLOSED, DIRECTIONS, IN, OPEN, OUT
from src.pricing import resolve_prices

MAX_QUANTITY = 9999


def _as_uuid(value) -> uuid.UUID:
    return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))


def _clean(value: str | None) -> str | None:
    """Trim a free-text field; empty -> None, matching ``collection_card``'s nullable columns."""
    v = (value or "").strip()
    return v or None


def _eq_or_null(col, value):
    return col.is_(None) if value is None else col == value


def _norm_finish(value: str | None) -> str:
    return value if value in ("normal", "foil", "etched") else "normal"


def _norm_language(value: str | None) -> str:
    return (value or "en").strip().lower() or "en"


# --- pools ---------------------------------------------------------------------------------------

async def create_pool(
    session: AsyncSession,
    name: str,
    *,
    partner: str | None = None,
    currency: str = "usd",
    price_source: str = "tcgplayer",
    note: str | None = None,
) -> TradePool:
    """Open a pool, freezing the valuation basis it will be read on (see the module docstring)."""
    pool = TradePool(
        name=(name or "").strip()[:256] or "Untitled trade",
        partner=(_clean(partner) or "")[:256] or None,
        currency=currency,
        price_source=price_source,
        note=_clean(note),
        status=OPEN,
    )
    session.add(pool)
    await session.commit()
    await session.refresh(pool)
    return pool


async def get_pool(session: AsyncSession, pool_id: int) -> TradePool | None:
    return await session.get(TradePool, pool_id)


async def open_pools(session: AsyncSession) -> list[TradePool]:
    """Pools still accepting cards — what the "stage this card" pickers offer."""
    return list(
        (
            await session.execute(
                select(TradePool).where(TradePool.status == OPEN)
                .order_by(TradePool.created_at.desc())
            )
        ).scalars().all()
    )


async def update_pool(
    session: AsyncSession, pool_id: int, *, name=None, partner=None, note=None, status=None
) -> TradePool | None:
    pool = await session.get(TradePool, pool_id)
    if pool is None:
        return None
    if name is not None and name.strip():
        pool.name = name.strip()[:256]
    if partner is not None:
        pool.partner = (_clean(partner) or "")[:256] or None
    if note is not None:
        pool.note = _clean(note)
    if status in (OPEN, CLOSED):
        pool.status = status
    await session.commit()
    await session.refresh(pool)
    return pool


async def delete_pool(session: AsyncSession, pool_id: int) -> bool:
    """Discard a pool and everything staged in it. Nothing in the collection is touched."""
    pool = await session.get(TradePool, pool_id)
    if pool is None:
        return False
    await session.delete(pool)
    await session.commit()
    return True


# --- staging -------------------------------------------------------------------------------------

async def stage_printing(
    session: AsyncSession,
    pool_id: int,
    direction: str,
    scryfall_id,
    quantity: int = 1,
    *,
    finish: str = "normal",
    condition: str | None = None,
    language: str = "en",
    collection_card_id: int | None = None,
) -> TradePoolItem | None:
    """Stage one copy-kind into a pool, merging with an identical item already staged.

    Returns None if the pool or the printing is unknown, so a bad id is a no-op rather than an
    orphaned item. Quantity is not checked against what's owned — see :func:`pool_view`, which
    reports shortfalls instead, because a pool is a *proposal* and the collection can change under
    it between staging and committing.
    """
    if direction not in DIRECTIONS:
        return None
    pool = await session.get(TradePool, pool_id)
    if pool is None:
        return None
    sid = _as_uuid(scryfall_id)
    if await session.get(Card, sid) is None:
        return None

    finish, condition = _norm_finish(finish), _clean(condition)
    language = _norm_language(language)
    quantity = min(max(1, quantity), MAX_QUANTITY)

    item = (
        await session.execute(
            select(TradePoolItem).where(
                TradePoolItem.pool_id == pool_id,
                TradePoolItem.direction == direction,
                TradePoolItem.scryfall_id == sid,
                TradePoolItem.finish == finish,
                TradePoolItem.language == language,
                _eq_or_null(TradePoolItem.condition, condition),
            )
        )
    ).scalar_one_or_none()

    if item is None:
        item = TradePoolItem(
            pool_id=pool_id, direction=direction, scryfall_id=sid, quantity=quantity,
            finish=finish, condition=condition, language=language,
            collection_card_id=collection_card_id,
        )
        session.add(item)
    else:
        item.quantity = min(item.quantity + quantity, MAX_QUANTITY)
    await session.commit()
    await session.refresh(item)
    return item


async def stage_stack(
    session: AsyncSession, pool_id: int, stack_id: int, quantity: int = 1
) -> TradePoolItem | None:
    """Stage an owned stack as outgoing, inheriting its finish / condition / language.

    This is the selector's main gesture: the stack is what the user actually pointed at, and its
    attributes are what make one copy worth more than another.
    """
    stack = await session.get(CollectionCard, stack_id)
    if stack is None:
        return None
    return await stage_printing(
        session, pool_id, OUT, stack.scryfall_id, quantity,
        finish=stack.finish, condition=stack.condition, language=stack.language,
        collection_card_id=stack.id,
    )


async def remove_item(session: AsyncSession, item_id: int) -> bool:
    """Unstage a card. False if there was no such item — missing is not an error, the pool just
    already looks the way the caller asked for."""
    item = await session.get(TradePoolItem, item_id)
    if item is None:
        return False
    await session.delete(item)
    await session.commit()
    return True


async def update_item(
    session: AsyncSession,
    item_id: int,
    *,
    quantity: int | None = None,
    finish: str | None = None,
    condition: str | None = None,
    language: str | None = None,
) -> bool:
    """Correct a staged copy — quantity and the attributes that decide what it's worth.

    Re-keying an item can collide with one already staged (stage a foil, then mark a second entry
    foil too): the two are merged rather than left as a duplicate the unique constraint would
    reject, mirroring how :func:`stage_printing` merges on the way in.
    """
    item = await session.get(TradePoolItem, item_id)
    if item is None:
        return False
    if quantity is not None and quantity <= 0:
        await session.delete(item)
        await session.commit()
        return True

    if quantity is not None:
        item.quantity = min(quantity, MAX_QUANTITY)
    if finish is not None:
        item.finish = _norm_finish(finish)
    if condition is not None:
        item.condition = _clean(condition)
    if language is not None:
        item.language = _norm_language(language)

    twin = (
        await session.execute(
            select(TradePoolItem).where(
                TradePoolItem.pool_id == item.pool_id,
                TradePoolItem.direction == item.direction,
                TradePoolItem.scryfall_id == item.scryfall_id,
                TradePoolItem.finish == item.finish,
                TradePoolItem.language == item.language,
                TradePoolItem.id != item.id,
                _eq_or_null(TradePoolItem.condition, item.condition),
            )
        )
    ).scalar_one_or_none()
    if twin is not None:
        twin.quantity = min(twin.quantity + item.quantity, MAX_QUANTITY)
        await session.delete(item)
    await session.commit()
    return True


async def clear_pool(session: AsyncSession, pool_id: int, direction: str | None = None) -> int:
    """Unstage everything (or one side). Returns how many items were removed."""
    stmt = select(TradePoolItem).where(TradePoolItem.pool_id == pool_id)
    if direction in DIRECTIONS:
        stmt = stmt.where(TradePoolItem.direction == direction)
    items = list((await session.execute(stmt)).scalars().all())
    for item in items:
        await session.delete(item)
    await session.commit()
    return len(items)


# --- reading a pool ------------------------------------------------------------------------------

@dataclass
class PoolRow:
    item_id: int
    direction: str
    scryfall_id: str
    name: str
    set_code: str
    collector_number: str
    rarity: str | None
    finish: str
    condition: str | None
    language: str
    quantity: int
    owned: int      # copies of this exact copy-kind currently in the collection (0 for incoming)
    unit: float

    @property
    def value(self) -> float:
        return round(self.quantity * self.unit, 2)

    @property
    def short(self) -> int:
        """Copies staged out that aren't (or are no longer) in the collection."""
        return max(0, self.quantity - self.owned) if self.direction == OUT else 0


@dataclass
class PoolSide:
    rows: list[PoolRow] = field(default_factory=list)

    @property
    def cards(self) -> int:
        return sum(r.quantity for r in self.rows)

    @property
    def value(self) -> float:
        return round(sum(r.value for r in self.rows), 2)


@dataclass
class PoolView:
    pool: TradePool
    giving: PoolSide = field(default_factory=PoolSide)
    getting: PoolSide = field(default_factory=PoolSide)

    @property
    def delta(self) -> float:
        """Getting minus giving, on the pool's basis. Positive means the trade favours you."""
        return round(self.getting.value - self.giving.value, 2)

    @property
    def shortfalls(self) -> list[PoolRow]:
        """Outgoing rows the collection can no longer cover — staged, then sold/edited away."""
        return [r for r in self.giving.rows if r.short]

    @property
    def is_empty(self) -> bool:
        return not (self.giving.rows or self.getting.rows)


async def _owned_counts(session: AsyncSession, items: list[TradePoolItem]) -> dict[tuple, int]:
    """Owned quantity per ``(scryfall_id, finish, condition, language)`` for the staged printings.

    One grouped query rather than a lookup per row, and keyed on the copy-kind rather than on the
    originating stack id — the same re-resolution the commit will do (ADR 0002 D4).
    """
    sids = {i.scryfall_id for i in items if i.direction == OUT}
    if not sids:
        return {}
    rows = await session.execute(
        select(
            CollectionCard.scryfall_id, CollectionCard.finish, CollectionCard.condition,
            CollectionCard.language, func.sum(CollectionCard.quantity),
        )
        .where(CollectionCard.scryfall_id.in_(sids))
        .group_by(CollectionCard.scryfall_id, CollectionCard.finish, CollectionCard.condition,
                  CollectionCard.language)
    )
    return {(sid, finish, condition, language): int(qty or 0)
            for sid, finish, condition, language, qty in rows.all()}


async def pool_view(session: AsyncSession, pool: TradePool) -> PoolView:
    """Both sides of a pool, valued on the pool's own currency and price source.

    Items are re-read rather than taken off ``pool.items``: the caller's ``pool`` may have been
    loaded before the last staging call, and a view that quietly renders a stale trade is worse
    than a slightly more expensive one.
    """
    items = list(
        (
            await session.execute(
                select(TradePoolItem).where(TradePoolItem.pool_id == pool.id)
                .order_by(TradePoolItem.direction, TradePoolItem.id)
            )
        ).scalars().all()
    )
    owned = await _owned_counts(session, items)
    view = PoolView(pool=pool)

    for item in items:
        card = item.card
        row = PoolRow(
            item_id=item.id,
            direction=item.direction,
            scryfall_id=str(item.scryfall_id),
            name=card.name,
            set_code=card.set_code,
            collector_number=card.collector_number,
            rarity=card.rarity,
            finish=item.finish,
            condition=item.condition,
            language=item.language,
            quantity=item.quantity,
            owned=owned.get(
                (item.scryfall_id, item.finish, item.condition, item.language), 0
            ) if item.direction == OUT else 0,
            unit=unit_price(
                resolve_prices(card.prices, card.market_prices, pool.price_source),
                item.finish, pool.currency,
            ),
        )
        (view.giving if item.direction == OUT else view.getting).rows.append(row)

    for side in (view.giving, view.getting):
        side.rows.sort(key=lambda r: (r.value, r.name), reverse=True)
    return view


@dataclass
class PoolSummary:
    pool: TradePool
    out_cards: int
    in_cards: int


async def pool_summaries(session: AsyncSession) -> list[PoolSummary]:
    """Every pool with its two card counts — the list on the Trade tab."""
    counts = {
        (pid, direction): int(total or 0)
        for pid, direction, total in (
            await session.execute(
                select(TradePoolItem.pool_id, TradePoolItem.direction,
                       func.sum(TradePoolItem.quantity))
                .group_by(TradePoolItem.pool_id, TradePoolItem.direction)
            )
        ).all()
    }
    pools = (
        await session.execute(select(TradePool).order_by(TradePool.created_at.desc()))
    ).scalars().all()
    return [
        PoolSummary(pool=p, out_cards=counts.get((p.id, OUT), 0),
                    in_cards=counts.get((p.id, IN), 0))
        for p in pools
    ]


async def stage_selection(
    session: AsyncSession, pool_id: int, direction: str, scryfall_ids: list[str],
    quantity: int = 1,
) -> int:
    """Bulk-stage printings selected in the results grid. Returns how many items were staged.

    The grid selects *printings*, but value follows the physical copy. Outgoing printings are
    therefore staged from the largest stack owned of each — the copy a bulk add most likely means.
    An outgoing printing that isn't owned (and anything incoming, which by definition isn't) is
    staged as a plain normal/English copy; that is the only honest guess available, and an
    outgoing one shows up as a shortfall in :func:`pool_view` until it's corrected.
    """
    staged = 0
    for raw in scryfall_ids:
        try:
            sid = _as_uuid(raw)
        except (ValueError, AttributeError, TypeError):
            continue
        stack = None
        if direction == OUT:
            stack = (
                await session.execute(
                    select(CollectionCard)
                    .where(CollectionCard.scryfall_id == sid)
                    .order_by(CollectionCard.quantity.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
        item = (
            await stage_stack(session, pool_id, stack.id, quantity) if stack
            else await stage_printing(session, pool_id, direction, sid, quantity)
        )
        staged += 1 if item is not None else 0
    return staged
