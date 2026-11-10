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
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.db import Base
from src.models.card import Card
from src.models.stack import Stack


class CollectionCard(Stack, Base):
    __tablename__ = "collection_card"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    ## Inheritied from Stack
    # scryfall_id: Mapped[uuid.UUID]
    # quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    # finish: Mapped[str] = mapped_column(String(16), nullable=False, default="normal")  # normal | foil | etched
    # language: Mapped[str] = mapped_column(String(8), nullable=False, default="en")
    
    condition: Mapped[str | None] = mapped_column(String(32))
    purchase_price: Mapped[float | None] = mapped_column(Float)
    source_format: Mapped[str | None] = mapped_column(String(32))  # manabox | dragonshield | delver
    source_location_name: Mapped[str | None] = mapped_column(String(256))
    location: Mapped[int | None] = mapped_column(Integer)  # physical storage place

    # User-defined labels (e.g. "for-trade", "deck:goblins"), searchable via `tag:`.
    tags: Mapped[list[str] | None] = mapped_column(ARRAY(String(64)))

    # Grading / slabbing (#179): a graded copy is metadata + a manual value (graded prices aren't in
    # Scryfall). ``grade_photo`` is a filename under ``<data_dir>/grades``.
    grade_company: Mapped[str | None] = mapped_column(String(16))  # PSA | BGS | CGC | SGC | …
    grade: Mapped[str | None] = mapped_column(String(16))          # "10", "9.5", …
    cert_number: Mapped[str | None] = mapped_column(String(32))
    grade_photo: Mapped[str | None] = mapped_column(String(128))
    value_override: Mapped[float | None] = mapped_column(Float)    # manual USD value for this stack

    added_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    # Optimistic-concurrency guard (#207). One collection is deliberately shared by more than one
    # person (ADR 0001), so an absolute edit — "set the quantity to 4", "this is Lightly Played",
    # "delete this stack" — must not silently overwrite someone else's. Every mutation bumps this;
    # absolute edits send the value they were shown and are refused (409) if it has moved since.
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))

    card: Mapped[Card] = relationship(lazy="joined")

    __table_args__ = (
        UniqueConstraint(
            "scryfall_id",
            "finish",
            "condition",
            "language",
            "location",
            name="uq_collection_stack",
        ),
        Index("ix_collection_card_tags_gin", "tags", postgresql_using="gin"),
    )


    def is_identical_to_stack(self, collection_card_2: CollectionCard) -> bool:
        """Check if two CollectionCards are identical (excluding id, source_format, and version).
        
        Two cards are considered identical if they have the same:
        - scryfall_id
        - quantity
        - finish
        - condition
        - language
        - purchase_price
        - location (this is ignored for comparison)
        - tags
        - grade_company
        - grade
        - cert_number
        - grade_photo
        - value_override

        Use this function only for backend stack operations, such as cleaning up split stacks or recombining stacks.
        Use Stack.get_unique_stack_key() instead for substituable identical printings, for example for deck coverage.
        """
        return (
            self.scryfall_id == collection_card_2.scryfall_id and
            self.quantity == collection_card_2.quantity and
            self.finish == collection_card_2.finish and
            self.condition == collection_card_2.condition and
            self.language == collection_card_2.language and
            self.purchase_price == collection_card_2.purchase_price and
            self.tags == collection_card_2.tags and
            self.grade_company == collection_card_2.grade_company and
            self.grade == collection_card_2.grade and
            self.cert_number == collection_card_2.cert_number and
            self.grade_photo == collection_card_2.grade_photo and
            self.value_override == collection_card_2.value_override
        )