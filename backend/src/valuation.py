"""Collection valuation report (#97): a printable value summary for insurance/records.

Reuses the owned collection + current prices to break value down by rarity and set, list the top
cards by value, and total everything — computed in Python for currency correctness (mirrors stats).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.currency import unit_price
from src.models import Card, CollectionCard

_RARITY_ORDER = ["mythic", "rare", "special", "bonus", "uncommon", "common"]


@dataclass
class ValueRow:
    label: str
    value: float
    count: int


@dataclass
class ValuedCard:
    name: str
    set_code: str
    scryfall_id: str
    quantity: int
    unit: float

    @property
    def value(self) -> float:
        return round(self.quantity * self.unit, 2)


@dataclass
class ValuationReport:
    total_value: float = 0.0
    total_cards: int = 0
    distinct_cards: int = 0
    printings: int = 0
    by_rarity: list[ValueRow] = field(default_factory=list)
    by_set: list[ValueRow] = field(default_factory=list)
    top_cards: list[ValuedCard] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return self.total_cards == 0


async def valuation_report(
    session: AsyncSession, currency: str = "usd", top: int = 25, top_sets: int = 15
) -> ValuationReport:
    rows = (
        await session.execute(
            select(
                CollectionCard.quantity, CollectionCard.finish,
                Card.rarity, Card.set_code, Card.set_name, Card.prices,
                Card.name, Card.oracle_id, Card.scryfall_id,
            ).join(Card, Card.scryfall_id == CollectionCard.scryfall_id)
        )
    ).all()

    r = ValuationReport()
    rarities: dict[str, list[float]] = {}          # label -> [value, count]
    sets: dict[str, list] = {}                     # set_code -> [value, count, name]
    printings: set = set()
    oracles: set = set()
    best: dict[str, ValuedCard] = {}               # per printing, its highest-unit stack

    for qty, finish, rarity, set_code, set_name, prices, name, oracle_id, sid in rows:
        qty = qty or 0
        unit = unit_price(prices, finish, currency)
        value = qty * unit
        r.total_cards += qty
        r.total_value += value
        printings.add(sid)
        if oracle_id:
            oracles.add(oracle_id)

        key = rarity or "unknown"
        entry = rarities.setdefault(key, [0.0, 0])
        entry[0] += value
        entry[1] += qty

        s = sets.setdefault(set_code, [0.0, 0, set_name or set_code.upper()])
        s[0] += value
        s[1] += qty

        cur = best.get(str(sid))
        if cur is None:
            best[str(sid)] = ValuedCard(name, set_code.upper(), str(sid), qty, unit)
        else:
            cur.quantity += qty
            cur.unit = max(cur.unit, unit)

    def _rarity_rank(kv) -> int:
        return _RARITY_ORDER.index(kv[0]) if kv[0] in _RARITY_ORDER else len(_RARITY_ORDER)

    r.printings = len(printings)
    r.distinct_cards = len(oracles)
    r.total_value = round(r.total_value, 2)
    r.by_rarity = [
        ValueRow(k, round(v, 2), c)
        for k, (v, c) in sorted(rarities.items(), key=_rarity_rank)
    ]
    top_sets_sorted = sorted(sets.items(), key=lambda kv: kv[1][0], reverse=True)[:top_sets]
    r.by_set = [ValueRow(name, round(v, 2), c) for _, (v, c, name) in top_sets_sorted]
    r.top_cards = sorted(best.values(), key=lambda c: c.value, reverse=True)[:top]
    return r
