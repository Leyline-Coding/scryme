"""The `wishlist` table: cards the user wants to acquire (separate from owned cards).

One row per printing (``scryfall_id``). ``quantity`` is how many you want; ``note`` is free text
(e.g. "foil for the Goblins deck").
"""

from __future__ import annotations

import datetime
import uuid

from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.db import Base
from src.models.card import Card


class WishlistItem(Base):
    __tablename__ = "wishlist"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    scryfall_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cards.scryfall_id", ondelete="CASCADE"), index=True
    )
    quantity: Mapped[int] = mapped_column(Integer, default=1)
    note: Mapped[str | None] = mapped_column(String(256))

    added_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    card: Mapped[Card] = relationship(lazy="joined")

    __table_args__ = (
        UniqueConstraint("scryfall_id", name="uq_wishlist_printing"),
    )
