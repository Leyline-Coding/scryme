"""Decks: named lists of wanted cards, compared against the owned collection.

A deck card is matched to the collection by ``oracle_id`` (any printing you own counts), so the
"what am I missing" view reflects ownership regardless of which printing is in the list. The
representative ``scryfall_id`` is kept only for display (image/name). Unrecognized lines keep
their text with null ids.
"""

from __future__ import annotations

from typing import Optional
from typing import TYPE_CHECKING
import datetime
import uuid

from sqlalchemy import DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship, foreign

from src.db import Base
from src.models.location import Location
from src.models.sleeve import Sleeve

if TYPE_CHECKING: from src.models.deck_card import DeckCard

class Deck(Base):
    __tablename__ = "deck"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(256))
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime.datetime] =mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    # Manual Commander bracket override (1–5). NULL = use the computed estimate (#159).
    bracket_override: Mapped[int | None] = mapped_column(Integer)
    # How the deck relates to the collection (#298): 'none' (unowned), 'full', or 'partial'.
    # When full/partial, edits to owned cards mirror into the collection.
    ownership: Mapped[str] = mapped_column(String(8), nullable=False, server_default="none")
    # Where the deck was imported from, so its printings can be re-synced from the source later.
    source_url: Mapped[str | None] = mapped_column(String(512))

    cards: Mapped[list['DeckCard']] = relationship(
        back_populates="deck", cascade="all, delete-orphan", lazy="selectin"
    )
    sleeves: Mapped[list[Sleeve]] = relationship(
        back_populates="deck", cascade="all, delete-orphan", lazy="selectin"
    )
    location: Mapped[Location] = relationship(
        "Location",
        primaryjoin=lambda: (
            (Deck.id == foreign(Location.foreign_id)) &
            (Location.type == "deck")
        ),
        uselist=False,
        viewonly=True,
    )