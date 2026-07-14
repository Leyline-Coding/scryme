"""Price history: capture snapshots of the owned collection and read value/movers from them.

`snapshot_prices` records the collection's current value (foil-aware) plus each owned printing's
market USD. `value_series` returns the value-over-time points; `biggest_movers` diffs the two most
recent snapshots to find the cards whose price changed most.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db import SessionLocal
from src.models import Card, CardPricePoint, CollectionCard, PriceSnapshot


def _f(value) -> float:
    try:
        return float(value) if value else 0.0
    except (TypeError, ValueError):
        return 0.0


def _unit_market(prices: dict | None, finish: str) -> float:
    """Current market USD for one card, preferring the foil price for foil/etched finishes."""
    prices = prices or {}
    foil = _f(prices.get("usd_foil"))
    usd = _f(prices.get("usd"))
    return foil if finish in ("foil", "etched") and foil else usd


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
class ChartPoint:
    x: float
    y: float
    value: float
    date: str


@dataclass
class ValueChart:
    """SVG-ready coordinates for a value-over-time line chart, built from `value_series`."""

    points: list[ChartPoint] = field(default_factory=list)
    polyline: str = ""   # "x,y x,y ..." for <polyline points=...>
    area: str = ""       # path data filling under the line down to the baseline
    min_value: float = 0.0
    max_value: float = 0.0
    current: float = 0.0
    width: int = 0
    height: int = 0

    @property
    def available(self) -> bool:
        return bool(self.points)

    @property
    def has_trend(self) -> bool:
        return len(self.points) >= 2

    @property
    def first_date(self) -> str:
        return self.points[0].date if self.points else ""

    @property
    def last_date(self) -> str:
        return self.points[-1].date if self.points else ""


def build_value_chart(
    series: list[PriceSnapshot], width: int = 1000, height: int = 140, pad: int = 8
) -> ValueChart:
    """Project a (chronological) snapshot series onto an SVG viewBox.

    Higher values map nearer the top (SVG y grows downward). A flat series sits on the midline;
    a single snapshot yields one centered point with no trend line.
    """
    pts = [(float(s.total_usd or 0), s.captured_at) for s in series]
    if not pts:
        return ValueChart(width=width, height=height)

    values = [v for v, _ in pts]
    vmin, vmax = min(values), max(values)
    flat = vmax == vmin
    span = vmax - vmin
    n = len(pts)
    inner_w = width - 2 * pad
    inner_h = height - 2 * pad

    coords: list[ChartPoint] = []
    for i, (v, dt) in enumerate(pts):
        x = pad + (inner_w * i / (n - 1) if n > 1 else inner_w / 2)
        norm = 0.5 if flat else (v - vmin) / span  # flat series sits on the midline
        y = pad + inner_h * (1 - norm)
        coords.append(ChartPoint(x=round(x, 1), y=round(y, 1), value=v,
                                 date=dt.strftime("%Y-%m-%d")))

    polyline = " ".join(f"{c.x},{c.y}" for c in coords)
    area = (
        f"M {coords[0].x},{height} "
        + " ".join(f"L {c.x},{c.y}" for c in coords)
        + f" L {coords[-1].x},{height} Z"
    )
    return ValueChart(points=coords, polyline=polyline, area=area, min_value=vmin,
                      max_value=vmax, current=values[-1], width=width, height=height)


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


async def _movers_between(
    session: AsyncSession, prev_id: int, latest_id: int, limit: int,
) -> tuple[list[Mover], list[Mover]]:
    """Top gainers/losers between two snapshots, by absolute USD change."""
    def points(sid):
        return select(CardPricePoint.scryfall_id, CardPricePoint.usd).where(
            CardPricePoint.snapshot_id == sid
        )

    latest = {s: u for s, u in (await session.execute(points(latest_id))).all()}
    prev = {s: u for s, u in (await session.execute(points(prev_id))).all()}
    shared = [s for s in latest if s in prev and latest[s] != prev[s]]
    if not shared:
        return [], []
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
    return gainers[:limit], losers[:limit]


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
    gainers, losers = await _movers_between(session, prev_id, latest_id, limit)
    return Movers(previous_at=prev_at, latest_at=latest_at, gainers=gainers, losers=losers)


@dataclass
class Digest:
    """Period-over-period collection summary for the home 'movers' panel."""

    available: bool = False
    latest_at: object = None
    baseline_at: object = None
    start_value: float = 0.0
    end_value: float = 0.0
    gainers: list = field(default_factory=list)
    losers: list = field(default_factory=list)

    @property
    def delta(self) -> float:
        return round(self.end_value - self.start_value, 2)

    @property
    def pct(self) -> float:
        return round(100 * (self.end_value - self.start_value) / self.start_value, 1) \
            if self.start_value else 0.0


async def collection_digest(session: AsyncSession, days: int = 7, limit: int = 5) -> Digest:
    """Value change + top movers since ~`days` ago (or the previous snapshot if sparse)."""
    latest = (await session.execute(
        select(PriceSnapshot).order_by(desc(PriceSnapshot.captured_at)).limit(1)
    )).scalars().first()
    if latest is None:
        return Digest()
    cutoff = latest.captured_at - timedelta(days=days)
    baseline = (await session.execute(
        select(PriceSnapshot)
        .where(PriceSnapshot.captured_at <= cutoff, PriceSnapshot.id != latest.id)
        .order_by(desc(PriceSnapshot.captured_at)).limit(1)
    )).scalars().first()
    if baseline is None:  # not enough history for the window — fall back to the previous snapshot
        baseline = (await session.execute(
            select(PriceSnapshot).where(PriceSnapshot.id != latest.id)
            .order_by(desc(PriceSnapshot.captured_at)).limit(1)
        )).scalars().first()
    if baseline is None:
        return Digest()
    gainers, losers = await _movers_between(session, baseline.id, latest.id, limit)
    return Digest(
        available=True, latest_at=latest.captured_at, baseline_at=baseline.captured_at,
        start_value=float(baseline.total_usd or 0), end_value=float(latest.total_usd or 0),
        gainers=gainers, losers=losers,
    )


@dataclass
class PLCard:
    """A single owned stack's acquisition profit/loss."""

    name: str
    set_code: str
    scryfall_id: str
    finish: str
    quantity: int
    cost: float    # purchase price per card
    market: float  # current market price per card

    @property
    def unit_delta(self) -> float:
        return round(self.market - self.cost, 2)

    @property
    def total_delta(self) -> float:
        return round(self.quantity * (self.market - self.cost), 2)

    @property
    def pct(self) -> float:
        return round(100 * (self.market - self.cost) / self.cost, 1) if self.cost else 0.0


@dataclass
class ProfitLoss:
    """Acquisition P/L over the cards that carry a recorded purchase price."""

    cost_basis: float = 0.0     # sum(qty * purchase_price) over valued stacks
    market_value: float = 0.0   # sum(qty * current market) over those same stacks
    priced_stacks: int = 0      # stacks with both a purchase price and a market price
    priced_cards: int = 0       # sum of quantities across those stacks
    unpriced_stacks: int = 0    # stacks with no purchase price (or no current market price)
    winners: list = field(default_factory=list)
    losers: list = field(default_factory=list)

    @property
    def unrealized(self) -> float:
        return round(self.market_value - self.cost_basis, 2)

    @property
    def pct(self) -> float:
        return round(100 * self.unrealized / self.cost_basis, 1) if self.cost_basis else 0.0

    @property
    def available(self) -> bool:
        return self.priced_stacks > 0


async def collection_pl(session: AsyncSession, limit: int = 10) -> ProfitLoss:
    """Compare each owned stack's purchase price to its current market value.

    Only stacks that have *both* a recorded purchase price and a current market price count
    toward the totals (you can't value the rest); the remainder are reported as ``unpriced``.
    """
    rows = (
        await session.execute(
            select(
                CollectionCard.quantity, CollectionCard.finish, CollectionCard.purchase_price,
                Card.prices, Card.name, Card.set_code, Card.scryfall_id,
            ).join(Card, Card.scryfall_id == CollectionCard.scryfall_id)
        )
    ).all()

    pl = ProfitLoss()
    cards: list[PLCard] = []
    for qty, finish, purchase, prices, name, set_code, sid in rows:
        qty = qty or 0
        if not qty:
            continue
        market = _unit_market(prices, finish)
        if purchase is None or market <= 0:
            pl.unpriced_stacks += 1
            continue
        cost = _f(purchase)
        pl.priced_stacks += 1
        pl.priced_cards += qty
        pl.cost_basis += qty * cost
        pl.market_value += qty * market
        cards.append(PLCard(name=name, set_code=(set_code or "").upper(), scryfall_id=str(sid),
                            finish=finish, quantity=qty, cost=cost, market=market))

    pl.cost_basis = round(pl.cost_basis, 2)
    pl.market_value = round(pl.market_value, 2)
    pl.winners = sorted(
        [c for c in cards if c.total_delta > 0], key=lambda c: c.total_delta, reverse=True
    )[:limit]
    pl.losers = sorted(
        [c for c in cards if c.total_delta < 0], key=lambda c: c.total_delta
    )[:limit]
    return pl
