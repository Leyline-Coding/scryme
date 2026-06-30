"""Price watchlist (#88): per-card USD thresholds, evaluated on each price refresh.

Mirrors the saved-search alert plumbing (#58): targets are re-evaluated when prices refresh, the
crossing state is recorded, and the triggered set is surfaced (home panel + badge) and counted for
desktop notifications. A target fires while its condition holds and resets when the price moves
back, so it can fire again on the next crossing.
"""

from __future__ import annotations

import datetime
import uuid
from dataclasses import dataclass

import structlog
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db import SessionLocal
from src.models import Card, PriceTarget

log = structlog.get_logger()

DIRECTIONS = {"below", "above"}


def _usd(prices: dict | None) -> float:
    try:
        return float((prices or {}).get("usd") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _condition_met(direction: str, price: float, threshold: float) -> bool:
    if price <= 0:
        return False
    return price <= threshold if direction == "below" else price >= threshold


@dataclass
class TargetRow:
    id: int
    scryfall_id: str
    name: str
    image_status: str
    direction: str
    threshold: float
    price: float
    triggered: bool


async def add_target(
    session: AsyncSession, scryfall_id: str, direction: str, threshold: float
) -> PriceTarget | None:
    if direction not in DIRECTIONS or threshold <= 0:
        return None
    try:
        sid = uuid.UUID(scryfall_id)
    except (ValueError, TypeError):
        return None
    if await session.get(Card, sid) is None:
        return None
    target = PriceTarget(scryfall_id=sid, direction=direction, threshold=round(threshold, 2))
    session.add(target)
    await session.commit()
    await session.refresh(target)
    return target


async def remove_target(session: AsyncSession, target_id: int) -> None:
    await session.execute(delete(PriceTarget).where(PriceTarget.id == target_id))
    await session.commit()


async def target_for(session: AsyncSession, scryfall_id: str) -> PriceTarget | None:
    try:
        sid = uuid.UUID(scryfall_id)
    except (ValueError, TypeError):
        return None
    return await session.scalar(
        select(PriceTarget).where(PriceTarget.scryfall_id == sid).limit(1)
    )


async def evaluate_targets(session: AsyncSession | None = None) -> int:
    """Re-evaluate every target against current prices. Returns the count newly triggered."""
    own = session is None
    session = session or SessionLocal()
    newly = 0
    try:
        targets = (await session.execute(select(PriceTarget))).scalars().all()
        if targets:
            prices_by_sid = dict(
                (
                    await session.execute(
                        select(Card.scryfall_id, Card.prices).where(
                            Card.scryfall_id.in_([t.scryfall_id for t in targets])
                        )
                    )
                ).all()
            )
            now = datetime.datetime.now(datetime.UTC)
            for t in targets:
                price = _usd(prices_by_sid.get(t.scryfall_id))
                if _condition_met(t.direction, price, t.threshold):
                    if t.triggered_at is None:
                        newly += 1
                    t.triggered_at = now
                    t.triggered_price = price
                else:
                    t.triggered_at = None
                    t.triggered_price = None
            await session.commit()
    finally:
        if own:
            await session.close()
    if newly:
        log.info("price_watch.triggered", count=newly)
    return newly


async def list_targets(session: AsyncSession) -> list[TargetRow]:
    rows = (
        await session.execute(
            select(
                PriceTarget.id, PriceTarget.scryfall_id, PriceTarget.direction,
                PriceTarget.threshold, PriceTarget.triggered_at, Card.name, Card.prices,
                Card.image_status,
            )
            .join(Card, Card.scryfall_id == PriceTarget.scryfall_id)
            .order_by(PriceTarget.triggered_at.desc().nulls_last(), Card.name)
        )
    ).all()
    out: list[TargetRow] = []
    for tid, sid, direction, threshold, triggered_at, name, prices, image_status in rows:
        out.append(
            TargetRow(
                id=tid, scryfall_id=str(sid), name=name, image_status=image_status or "pending",
                direction=direction, threshold=threshold, price=_usd(prices),
                triggered=triggered_at is not None,
            )
        )
    return out


async def count_triggered(session: AsyncSession) -> int:
    return int(
        await session.scalar(
            select(func.count()).select_from(PriceTarget).where(PriceTarget.triggered_at.isnot(None))
        )
        or 0
    )


async def triggered_targets(session: AsyncSession) -> list[TargetRow]:
    return [t for t in await list_targets(session) if t.triggered]
