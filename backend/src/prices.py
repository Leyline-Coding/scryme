"""Price history: capture snapshots of the owned collection and read value/movers from them.

`snapshot_prices` records the collection's current value (foil-aware) plus each owned printing's
market USD. `value_series` returns the value-over-time points; `biggest_movers` diffs the two most
recent snapshots to find the cards whose price changed most.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db import SessionLocal
from src.models import Card, CardPricePoint, CollectionCard, PriceSnapshot


def _f(value) -> float:
    try:
        return float(value) if value else 0.0
    except (TypeError, ValueError):
        return 0.0


async def snapshot_prices(session: AsyncSession) -> PriceSnapshot | None:
    """Capture a price snapshot of the owned collection. Returns None if nothing is owned."""
    rows = (
        await session.execute(
            select(
                CollectionCard.scryfall_id, CollectionCard.quantity, CollectionCard.finish,
                Card.prices,
            ).join(Card, Card.scryfall_id == CollectionCard.scryfall_id)
        )
    ).all()
    if not rows:
        return None

    total = 0.0
    market: dict = {}  # scryfall_id -> market (non-foil) USD, for mover comparison
    for sid, qty, finish, prices in rows:
        prices = prices or {}
        usd = _f(prices.get("usd"))
        foil = _f(prices.get("usd_foil"))
        unit = foil if finish in ("foil", "etched") and foil else usd
        total += (qty or 0) * unit
        if usd > 0:
            market[sid] = usd

    snap = PriceSnapshot(total_usd=round(total, 2), card_count=len(market))
    session.add(snap)
    await session.flush()
    for sid, usd in market.items():
        session.add(CardPricePoint(snapshot_id=snap.id, scryfall_id=sid, usd=usd))
    await session.commit()
    await session.refresh(snap)
    return snap


async def take_snapshot() -> PriceSnapshot | None:
    """Open a session and capture a snapshot (used by the scheduler and CLI)."""
    async with SessionLocal() as session:
        return await snapshot_prices(session)


async def value_series(session: AsyncSession, limit: int = 90) -> list[PriceSnapshot]:
    """Most recent snapshots, oldest-first, for the value-over-time chart."""
    rows = (
        await session.execute(
            select(PriceSnapshot).order_by(desc(PriceSnapshot.captured_at)).limit(limit)
        )
    ).scalars().all()
    return list(reversed(rows))


@dataclass
class Mover:
    name: str
    set_code: str
    scryfall_id: str
    old: float
    new: float

    @property
    def delta(self) -> float:
        return round(self.new - self.old, 2)

    @property
    def pct(self) -> float:
        return round(100 * (self.new - self.old) / self.old, 1) if self.old else 0.0


@dataclass
class Movers:
    previous_at: object = None
    latest_at: object = None
    gainers: list = None
    losers: list = None

    @property
    def available(self) -> bool:
        return bool(self.gainers) or bool(self.losers)


async def biggest_movers(session: AsyncSession, limit: int = 10) -> Movers:
    """Compare the two most recent snapshots and return top gainers/losers by absolute change."""
    snaps = (
        await session.execute(
            select(PriceSnapshot.id, PriceSnapshot.captured_at)
            .order_by(desc(PriceSnapshot.captured_at))
            .limit(2)
        )
    ).all()
    if len(snaps) < 2:
        return Movers(gainers=[], losers=[])
    (latest_id, latest_at), (prev_id, prev_at) = snaps[0], snaps[1]

    def points(sid):
        return select(CardPricePoint.scryfall_id, CardPricePoint.usd).where(
            CardPricePoint.snapshot_id == sid
        )

    latest = {s: u for s, u in (await session.execute(points(latest_id))).all()}
    prev = {s: u for s, u in (await session.execute(points(prev_id))).all()}
    shared = [s for s in latest if s in prev and latest[s] != prev[s]]
    if not shared:
        return Movers(previous_at=prev_at, latest_at=latest_at, gainers=[], losers=[])

    names = {
        sid: (name, set_code)
        for sid, name, set_code in (
            await session.execute(
                select(Card.scryfall_id, Card.name, Card.set_code).where(
                    Card.scryfall_id.in_(shared)
                )
            )
        ).all()
    }
    movers = []
    for sid in shared:
        name, set_code = names.get(sid, ("?", ""))
        movers.append(Mover(name=name, set_code=(set_code or "").upper(),
                            scryfall_id=str(sid), old=prev[sid], new=latest[sid]))
    gainers = sorted([m for m in movers if m.delta > 0], key=lambda m: m.delta, reverse=True)
    losers = sorted([m for m in movers if m.delta < 0], key=lambda m: m.delta)
    return Movers(previous_at=prev_at, latest_at=latest_at,
                  gainers=gainers[:limit], losers=losers[:limit])
