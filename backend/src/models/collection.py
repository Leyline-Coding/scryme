"""The `collection_card` table: the single (implicit) user's owned cards.

Each row is a distinct stack of a printing distinguished by finish/condition/language/binder,
so merge strategies can increment quantities or replace stacks deterministically.
"""

from __future__ import annotations

import datetime
import uuid

from sqlalchemy import (
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.db import Base
from src.models.card import Card


class CollectionCard(Base):
    __tablename__ = "collection_card"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    scryfall_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cards.scryfall_id", ondelete="CASCADE"), index=True
    )

    quantity: Mapped[int] = mapped_column(Integer, default=1)
    finish: Mapped[str] = mapped_column(String(16), default="normal")  # normal | foil | etched
    condition: Mapped[str | None] = mapped_column(String(32))
    language: Mapped[str] = mapped_column(String(8), default="en")
    purchase_price: Mapped[float | None] = mapped_column(Float)
    binder_name: Mapped[str | None] = mapped_column(String(256))
    source_format: Mapped[str | None] = mapped_column(String(32))  # manabox | dragonshield | delver

    # User-defined labels (e.g. "for-trade", "deck:goblins"), searchable via `tag:`.
    tags: Mapped[list[str] | None] = mapped_column(ARRAY(String(64)))

    added_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    card: Mapped[Card] = relationship(lazy="joined")

    __table_args__ = (
        UniqueConstraint(
            "scryfall_id",
            "finish",
            "condition",
            "language",
            "binder_name",
            name="uq_collection_stack",
        ),
        Index("ix_collection_card_tags_gin", "tags", postgresql_using="gin"),
    )
