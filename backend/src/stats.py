"""Aggregate insights over the owned collection for the stats dashboard.

One query pulls every owned stack joined to its card; the breakdowns are computed in Python
(the collection is small) so we avoid array/type SQL gymnastics and keep it easy to test.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.currency import unit_price
from src.models import Card, CollectionCard

_COLOR_NAMES = {"W": "White", "U": "Blue", "B": "Black", "R": "Red", "G": "Green"}
# Primary card types, checked in this order (first match wins).
_TYPES = ["Creature", "Planeswalker", "Battle", "Instant", "Sorcery", "Artifact",
          "Enchantment", "Land"]
_RARITY_ORDER = ["common", "uncommon", "rare", "mythic", "special", "bonus"]
_MAX_MV_BUCKET = 7  # 7+ collapses into one bucket


@dataclass
class Bar:
    label: str
    count: int


@dataclass
class ValuedCard:
    name: str
    set_code: str
    scryfall_id: str
    usd: float


@dataclass
class CollectionStats:
    total_cards: int = 0          # sum of quantities
    printings: int = 0            # distinct printings owned
    distinct_cards: int = 0       # distinct oracle ids
    total_value: float = 0.0      # sum(qty * unit price)
    by_color: list[Bar] = field(default_factory=list)
    by_rarity: list[Bar] = field(default_factory=list)
    by_type: list[Bar] = field(default_factory=list)
    by_set: list[Bar] = field(default_factory=list)
    mana_curve: list[Bar] = field(default_factory=list)
    most_valuable: list[ValuedCard] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return self.total_cards == 0




def _primary_type(type_line: str | None) -> str:
    tl = type_line or ""
    for t in _TYPES:
        if t in tl:
            return t
    return "Other"


def _color_bucket(color_identity: list[str] | None) -> str:
    ci = color_identity or []
    if not ci:
        return "Colorless"
    if len(ci) > 1:
        return "Multicolor"
    return _COLOR_NAMES.get(ci[0], ci[0])


def _bars(
    counts: dict[str, int], order: list[str] | None = None, top: int | None = None
) -> list[Bar]:
    items = counts.items()
    if order is not None:
        items = sorted(items, key=lambda kv: order.index(kv[0]) if kv[0] in order else len(order))
    else:
        items = sorted(items, key=lambda kv: kv[1], reverse=True)
    bars = [Bar(label=k, count=v) for k, v in items if v]
    return bars[:top] if top else bars


@dataclass
class GrowthPoint:
    label: str   # "YYYY-MM"
    added: int   # cards added that month
    value: float # their value at current prices


@dataclass
class CollectionGrowth:
    points: list[GrowthPoint] = field(default_factory=list)  # oldest-first (recent window)
    total_added: int = 0
    total_value: float = 0.0
    total_months: int = 0  # distinct months with any activity (may exceed the shown window)

    @property
    def available(self) -> bool:
        return bool(self.points)

    @property
    def max_added(self) -> int:
        return max((p.added for p in self.points), default=0)

    @property
    def windowed(self) -> bool:
        return self.total_months > len(self.points)


async def collection_growth(
    session: AsyncSession, currency: str = "usd", months: int = 12
) -> CollectionGrowth:
    """Cards (and current-price value) added per month, from ``collection_card.added_at``.

    The chart shows the most recent ``months`` with activity; totals cover the whole history.
    """
    rows = (
        await session.execute(
            select(
                CollectionCard.added_at, CollectionCard.quantity, CollectionCard.finish,
                Card.prices,
            ).join(Card, Card.scryfall_id == CollectionCard.scryfall_id)
        )
    ).all()

    added: dict[str, int] = {}
    value: dict[str, float] = {}
    for added_at, qty, finish, prices in rows:
        if added_at is None:
            continue
        key = added_at.strftime("%Y-%m")
        qty = qty or 0
        added[key] = added.get(key, 0) + qty
        value[key] = value.get(key, 0.0) + qty * unit_price(prices, finish, currency)

    keys = sorted(added)
    window = keys[-months:] if months else keys
    points = [GrowthPoint(label=k, added=added[k], value=round(value[k], 2)) for k in window]
    return CollectionGrowth(
        points=points,
        total_added=sum(added.values()),
        total_value=round(sum(value.values()), 2),
        total_months=len(keys),
    )


async def collection_stats(session: AsyncSession, currency: str = "usd") -> CollectionStats:
    rows = (
        await session.execute(
            select(
                CollectionCard.quantity, CollectionCard.finish,
                Card.rarity, Card.color_identity, Card.type_line, Card.cmc, Card.prices,
                Card.name, Card.set_code, Card.set_name, Card.oracle_id, Card.scryfall_id,
            ).join(Card, Card.scryfall_id == CollectionCard.scryfall_id)
        )
    ).all()

    s = CollectionStats()
    colors: dict[str, int] = {}
    rarities: dict[str, int] = {}
    types: dict[str, int] = {}
    sets: dict[str, int] = {}
    curve: dict[str, int] = {}
    printings: set = set()
    oracles: set = set()
    valued: dict[str, ValuedCard] = {}

    for (qty, finish, rarity, color_identity, type_line, cmc, prices,
         name, set_code, set_name, oracle_id, sid) in rows:
        qty = qty or 0
        s.total_cards += qty
        printings.add(sid)
        if oracle_id:
            oracles.add(oracle_id)

        unit = unit_price(prices, finish, currency)
        s.total_value += qty * unit

        colors[_color_bucket(color_identity)] = colors.get(_color_bucket(color_identity), 0) + qty
        if rarity:
            rarities[rarity] = rarities.get(rarity, 0) + qty
        types[_primary_type(type_line)] = types.get(_primary_type(type_line), 0) + qty
        label = (set_name or set_code.upper())
        sets[label] = sets.get(label, 0) + qty
        bucket = f"{_MAX_MV_BUCKET}+" if (cmc or 0) >= _MAX_MV_BUCKET else str(int(cmc or 0))
        curve[bucket] = curve.get(bucket, 0) + qty

        if unit > 0 and (sid not in valued or unit > valued[str(sid)].usd):
            valued[str(sid)] = ValuedCard(name=name, set_code=set_code.upper(),
                                          scryfall_id=str(sid), usd=unit)

    s.printings = len(printings)
    s.distinct_cards = len(oracles)
    s.by_color = _bars(colors)
    s.by_rarity = _bars(rarities, order=_RARITY_ORDER)
    s.by_type = _bars(types)
    s.by_set = _bars(sets, top=10)
    curve_order = [str(i) for i in range(_MAX_MV_BUCKET)] + [f"{_MAX_MV_BUCKET}+"]
    s.mana_curve = _bars(curve, order=curve_order)
    s.most_valuable = sorted(valued.values(), key=lambda v: v.usd, reverse=True)[:10]
    return s
