"""Trade pools: a staged, persisted list of what you're giving and getting in one trade (#331).

Distinct from ``src.trade``'s surplus binder, which is *derived* ("everything I own more than
`keep` copies of"). A pool is *chosen*: you pick specific stacks, in specific quantities, for one
specific trade, and it survives until you commit or clear it.

Two design constraints come from
:doc:`ADR 0002 </development/adr/0002-cross-device-trading>` and are load-bearing rather than
incidental:

* **Items are self-describing.** An item carries the printing *and* the finish/condition/language
  that determine its value, not just a ``collection_card`` row id. The row id is advisory — it
  records where a card was staged from, and is deliberately not the thing a commit addresses, so a
  pool stays meaningful after the underlying stack is edited, merged or deleted (D4), and so the
  pool can be serialized to another device without carrying this instance's primary keys (D1).
* **The valuation basis is frozen on the pool**, not read from whoever is looking. Two people
  reading the same trade must see the same totals (D2), so ``currency``/``price_source`` are
  captured when the pool is created and every value in it is computed on that basis.
"""

from __future__ import annotations

import datetime
import uuid

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.db import Base
from src.models.card import Card

# Which way a card is moving, from this collection's point of view.
OUT = "out"  # leaving: staged from an owned stack
IN = "in"    # arriving: not owned yet
DIRECTIONS = (OUT, IN)

OPEN = "open"
CLOSED = "closed"


class TradePool(Base):
    __tablename__ = "trade_pool"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(256))
    partner: Mapped[str | None] = mapped_column(String(256))  # who you're trading with, free text
    status: Mapped[str] = mapped_column(String(16), default=OPEN)  # open | closed
    note: Mapped[str | None] = mapped_column(Text)

    # The agreed valuation basis for this trade — see the module docstring (ADR 0002 D2).
    currency: Mapped[str] = mapped_column(String(8), default="usd")
    price_source: Mapped[str] = mapped_column(String(32), default="tcgplayer")

    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    items: Mapped[list[TradePoolItem]] = relationship(
        back_populates="pool", cascade="all, delete-orphan", lazy="selectin"
    )


class TradePoolItem(Base):
    __tablename__ = "trade_pool_item"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    pool_id: Mapped[int] = mapped_column(
        ForeignKey("trade_pool.id", ondelete="CASCADE"), index=True
    )
    direction: Mapped[str] = mapped_column(String(4))  # out | in

    scryfall_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cards.scryfall_id", ondelete="CASCADE"), index=True
    )
    quantity: Mapped[int] = mapped_column(Integer, default=1)

    # The value-bearing attributes of the physical copy, mirroring ``collection_card``.
    finish: Mapped[str] = mapped_column(String(16), default="normal")
    condition: Mapped[str | None] = mapped_column(String(32))
    language: Mapped[str] = mapped_column(String(8), default="en")

    # Where an outgoing card was staged from. Advisory only (see the module docstring): nulled
    # rather than cascaded if the stack goes away, so the item survives to be re-resolved.
    collection_card_id: Mapped[int | None] = mapped_column(
        ForeignKey("collection_card.id", ondelete="SET NULL")
    )

    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    pool: Mapped[TradePool] = relationship(back_populates="items")
    card: Mapped[Card] = relationship(lazy="joined")

    __table_args__ = (
        # Staging the same copy twice increments instead of duplicating. As with
        # ``uq_collection_stack`` this can't catch NULL ``condition`` collisions (Postgres treats
        # NULLs as distinct), so the service does the lookup explicitly; this is a backstop.
        UniqueConstraint(
            "pool_id", "direction", "scryfall_id", "finish", "condition", "language",
            name="uq_trade_pool_item",
        ),
    )
