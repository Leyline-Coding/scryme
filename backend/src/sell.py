"""Sell list: the cards you've flagged to sell, priced with a running total (#97).

A printing is on the sell list when any of its stacks carries a *for-sale* tag. Unlike the trade
binder (surplus beyond a keep count), selling is explicit — the whole owned quantity of a flagged
card is listed. Values use the active display currency.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.currency import unit_price
from src.models import Card, CollectionCard
from src.pricing import resolve_prices

# Tags (normalized) that mark a card for sale.
SELL_TAGS = ["for-sale", "for sale", "sale"]


@dataclass
class SellCard:
    scryfall_id: str
    name: str
    set_code: str
    set_name: str | None
    collector_number: str
    rarity: str | None
    quantity: int
    unit: float

    @property
    def value(self) -> float:
        return round(self.quantity * self.unit, 2)


@dataclass
class SellList:
    cards: list[SellCard] = field(default_factory=list)

    @property
    def total_value(self) -> float:
        return round(sum(c.value for c in self.cards), 2)

    @property
    def total_cards(self) -> int:
        return sum(c.quantity for c in self.cards)


async def sell_list(
    session: AsyncSession, currency: str = "usd", source: str = "tcgplayer"
) -> SellList:
    """Cards flagged for sale, most valuable first."""
    flagged = func.bool_or(CollectionCard.tags.op("&&")(SELL_TAGS))
    rows = (
        await session.execute(
            select(
                Card.scryfall_id, Card.name, Card.set_code, Card.set_name, Card.rarity,
                Card.collector_number, Card.prices, Card.market_prices,
                func.sum(CollectionCard.quantity).label("qty"),
            )
            .join(CollectionCard, CollectionCard.scryfall_id == Card.scryfall_id)
            .group_by(Card.scryfall_id)
            .having(flagged)
        )
    ).all()

    cards = [
        SellCard(
            scryfall_id=str(sid), name=name, set_code=set_code, set_name=set_name,
            collector_number=cn, rarity=rarity, quantity=int(qty or 0),
            unit=unit_price(resolve_prices(prices, market_prices, source), "normal", currency),
        )
        for sid, name, set_code, set_name, rarity, cn, prices, market_prices, qty in rows
    ]
    cards.sort(key=lambda c: (c.value, c.quantity), reverse=True)
    return SellList(cards=cards)
