"""Trade / surplus binder: the cards you have spare or have flagged for trade.

A printing is "tradeable" when you own more than `keep` copies (the spares beyond a set you keep)
or when any of its stacks is tagged for trade. Values use the active display currency.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.currency import unit_price
from src.models import Card, CollectionCard

# Tags (normalized) that mark a card for trade regardless of quantity.
TRADE_TAGS = ["for-trade", "for trade", "trade"]


@dataclass
class TradeCard:
    scryfall_id: str
    name: str
    set_code: str
    set_name: str | None
    collector_number: str
    rarity: str | None
    owned: int
    tradeable: int
    for_trade: bool   # flagged by tag (vs. surplus only)
    unit: float

    @property
    def value(self) -> float:
        return round(self.tradeable * self.unit, 2)


@dataclass
class TradeBinder:
    cards: list[TradeCard] = field(default_factory=list)
    keep: int = 1

    @property
    def total_value(self) -> float:
        return round(sum(c.value for c in self.cards), 2)

    @property
    def total_cards(self) -> int:
        return sum(c.tradeable for c in self.cards)


async def trade_binder(session: AsyncSession, currency: str = "usd", keep: int = 1) -> TradeBinder:
    """Cards owned in more than `keep` copies, plus any tagged for trade. Most valuable first."""
    keep = max(0, keep)
    flagged = func.coalesce(func.bool_or(CollectionCard.tags.op("&&")(TRADE_TAGS)), False)
    rows = (
        await session.execute(
            select(
                Card.scryfall_id, Card.name, Card.set_code, Card.set_name, Card.rarity,
                Card.collector_number, Card.prices,
                func.sum(CollectionCard.quantity).label("owned"),
                flagged.label("for_trade"),
            )
            .join(CollectionCard, CollectionCard.scryfall_id == Card.scryfall_id)
            .group_by(Card.scryfall_id)
        )
    ).all()

    cards: list[TradeCard] = []
    for sid, name, set_code, set_name, rarity, cn, prices, owned, for_trade in rows:
        owned = int(owned or 0)
        for_trade = bool(for_trade)
        tradeable = owned if for_trade else max(0, owned - keep)
        if tradeable <= 0:
            continue
        cards.append(
            TradeCard(
                scryfall_id=str(sid), name=name, set_code=set_code, set_name=set_name,
                collector_number=cn, rarity=rarity, owned=owned, tradeable=tradeable,
                for_trade=for_trade, unit=unit_price(prices, "normal", currency),
            )
        )
    cards.sort(key=lambda c: (c.value, c.owned), reverse=True)
    return TradeBinder(cards=cards, keep=keep)
