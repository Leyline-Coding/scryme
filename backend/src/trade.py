"""Trade / surplus binder: the cards you have spare or have flagged for trade.

A printing is "tradeable" when you own more than `keep` copies (the spares beyond a set you keep)
or when any of its stacks is tagged for trade. Values use the active display currency.

Optionally, stacks that sit in a deck are left out of the count (#408): nobody takes a deck apart
to meet a surplus quota. A stack explicitly tagged for trade still counts wherever it is.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.currency import unit_price
from src.models import Card, CollectionCard, Location
from src.pricing import resolve_prices

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
    exclude_decks: bool = False

    @property
    def total_value(self) -> float:
        return round(sum(c.value for c in self.cards), 2)

    @property
    def total_cards(self) -> int:
        return sum(c.tradeable for c in self.cards)


async def trade_binder(
    session: AsyncSession, currency: str = "usd", keep: int = 1, source: str = "tcgplayer",
    exclude_decks: bool = False,
) -> TradeBinder:
    """Cards owned in more than `keep` copies, plus any tagged for trade. Most valuable first.

    With `exclude_decks`, copies stored in a deck don't count towards what you own, so only the
    copies outside decks can be spare. Stacks tagged for trade are counted regardless.
    """
    keep = max(0, keep)
    tagged = func.coalesce(CollectionCard.tags.op("&&")(TRADE_TAGS), False)
    flagged = func.coalesce(func.bool_or(tagged), False)
    query = (
        select(
            Card.scryfall_id, Card.name, Card.set_code, Card.set_name, Card.rarity,
            Card.collector_number, Card.prices, Card.market_prices,
            func.sum(CollectionCard.quantity).label("owned"),
            flagged.label("for_trade"),
        )
        .join(CollectionCard, CollectionCard.scryfall_id == Card.scryfall_id)
        .group_by(Card.scryfall_id)
    )
    if exclude_decks:
        in_deck = select(Location.id).where(Location.type == "deck")
        query = query.where(
            or_(CollectionCard.location.is_(None), CollectionCard.location.not_in(in_deck), tagged)
        )
    rows = (await session.execute(query)).all()

    cards: list[TradeCard] = []
    for sid, name, set_code, set_name, rarity, cn, prices, market_prices, owned, for_trade in rows:
        owned = int(owned or 0)
        for_trade = bool(for_trade)
        tradeable = owned if for_trade else max(0, owned - keep)
        if tradeable <= 0:
            continue
        cards.append(
            TradeCard(
                scryfall_id=str(sid), name=name, set_code=set_code, set_name=set_name,
                collector_number=cn, rarity=rarity, owned=owned, tradeable=tradeable,
                for_trade=for_trade,
                unit=unit_price(resolve_prices(prices, market_prices, source), "normal", currency),
            )
        )
    cards.sort(key=lambda c: (c.value, c.owned), reverse=True)
    return TradeBinder(cards=cards, keep=keep, exclude_decks=exclude_decks)
