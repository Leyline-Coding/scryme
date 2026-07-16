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
from src.pricing import resolve_prices

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
    query: str | None = None  # collection search this bar links to (#206)


# Color-bucket label -> the search that reproduces it on the collection.
_COLOR_QUERIES = {
    "White": "ci=w", "Blue": "ci=u", "Black": "ci=b", "Red": "ci=r", "Green": "ci=g",
    "Colorless": "ci=c", "Multicolor": "ci:m",
}


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
    counts: dict[str, int], order: list[str] | None = None, top: int | None = None,
    query_for=None,
) -> list[Bar]:
    items = counts.items()
    if order is not None:
        items = sorted(items, key=lambda kv: order.index(kv[0]) if kv[0] in order else len(order))
    else:
        items = sorted(items, key=lambda kv: kv[1], reverse=True)
    bars = [Bar(label=k, count=v, query=query_for(k) if query_for else None)
            for k, v in items if v]
    return bars[:top] if top else bars


def _type_query(label: str) -> str | None:
    return f"t:{label.lower()}" if label != "Other" else None


def _curve_query(label: str) -> str:
    return f"cmc>={_MAX_MV_BUCKET}" if label.endswith("+") else f"cmc={label}"


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
    session: AsyncSession, currency: str = "usd", months: int = 12, source: str = "tcgplayer"
) -> CollectionGrowth:
    """Cards (and current-price value) added per month, from ``collection_card.added_at``.

    The chart shows the most recent ``months`` with activity; totals cover the whole history.
    """
    rows = (
        await session.execute(
            select(
                CollectionCard.added_at, CollectionCard.quantity, CollectionCard.finish,
                Card.prices, Card.market_prices,
            ).join(Card, Card.scryfall_id == CollectionCard.scryfall_id)
        )
    ).all()

    added: dict[str, int] = {}
    value: dict[str, float] = {}
    for added_at, qty, finish, prices, market_prices in rows:
        if added_at is None:  # pragma: no cover - added_at is NOT NULL
            continue
        key = added_at.strftime("%Y-%m")
        qty = qty or 0
        added[key] = added.get(key, 0) + qty
        value[key] = value.get(key, 0.0) + qty * unit_price(
            resolve_prices(prices, market_prices, source), finish, currency)

    keys = sorted(added)
    window = keys[-months:] if months else keys
    points = [GrowthPoint(label=k, added=added[k], value=round(value[k], 2)) for k in window]
    return CollectionGrowth(
        points=points,
        total_added=sum(added.values()),
        total_value=round(sum(value.values()), 2),
        total_months=len(keys),
    )


async def collection_stats(
    session: AsyncSession, currency: str = "usd", source: str = "tcgplayer"
) -> CollectionStats:
    rows = (
        await session.execute(
            select(
                CollectionCard.quantity, CollectionCard.finish,
                Card.rarity, Card.color_identity, Card.type_line, Card.cmc, Card.prices,
                Card.market_prices,
                Card.name, Card.set_code, Card.set_name, Card.oracle_id, Card.scryfall_id,
            ).join(Card, Card.scryfall_id == CollectionCard.scryfall_id)
        )
    ).all()

    s = CollectionStats()
    colors: dict[str, int] = {}
    rarities: dict[str, int] = {}
    types: dict[str, int] = {}
    sets: dict[str, int] = {}          # set_code -> qty
    set_names: dict[str, str] = {}     # set_code -> display name
    curve: dict[str, int] = {}
    printings: set = set()
    oracles: set = set()
    valued: dict[str, ValuedCard] = {}

    for (qty, finish, rarity, color_identity, type_line, cmc, prices, market_prices,
         name, set_code, set_name, oracle_id, sid) in rows:
        qty = qty or 0
        s.total_cards += qty
        printings.add(sid)
        if oracle_id:
            oracles.add(oracle_id)

        unit = unit_price(resolve_prices(prices, market_prices, source), finish, currency)
        s.total_value += qty * unit

        colors[_color_bucket(color_identity)] = colors.get(_color_bucket(color_identity), 0) + qty
        if rarity:
            rarities[rarity] = rarities.get(rarity, 0) + qty
        types[_primary_type(type_line)] = types.get(_primary_type(type_line), 0) + qty
        sets[set_code] = sets.get(set_code, 0) + qty
        set_names[set_code] = set_name or set_code.upper()
        bucket = f"{_MAX_MV_BUCKET}+" if (cmc or 0) >= _MAX_MV_BUCKET else str(int(cmc or 0))
        curve[bucket] = curve.get(bucket, 0) + qty

        if unit > 0 and (sid not in valued or unit > valued[str(sid)].usd):
            valued[str(sid)] = ValuedCard(name=name, set_code=set_code.upper(),
                                          scryfall_id=str(sid), usd=unit)

    s.printings = len(printings)
    s.distinct_cards = len(oracles)
    s.by_color = _bars(colors, query_for=_COLOR_QUERIES.get)
    s.by_rarity = _bars(rarities, order=_RARITY_ORDER, query_for=lambda r: f"r={r}")
    s.by_type = _bars(types, query_for=_type_query)
    top_sets = sorted(sets.items(), key=lambda kv: kv[1], reverse=True)[:10]
    s.by_set = [Bar(label=set_names[code], count=qty, query=f"s:{code}")
                for code, qty in top_sets if qty]
    curve_order = [str(i) for i in range(_MAX_MV_BUCKET)] + [f"{_MAX_MV_BUCKET}+"]
    s.mana_curve = _bars(curve, order=curve_order, query_for=_curve_query)
    s.most_valuable = sorted(valued.values(), key=lambda v: v.usd, reverse=True)[:10]
    return s
