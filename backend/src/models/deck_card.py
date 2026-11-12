"""Decks: named lists of wanted cards, compared against the owned collection.

A deck card is matched to the collection by ``oracle_id`` (any printing you own counts), so the
"what am I missing" view reflects ownership regardless of which printing is in the list. The
representative ``scryfall_id`` is kept only for display (image/name). Unrecognized lines keep
their text with null ids.
"""

from __future__ import annotations

import datetime
import uuid

from sqlalchemy import Boolean, ForeignKey, Integer, String, false
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.db import Base
from src.models.deck import Deck
from src.models.stack import Stack


class DeckCard(Stack, Base):
    __tablename__ = "deck_card"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    deck_id: Mapped[int] = mapped_column(
        ForeignKey("deck.id", ondelete="CASCADE"), index=True
    )

    ## Inheritied from Stack
    # scryfall_id: Mapped[uuid.UUID]
    # quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    # finish: Mapped[str] = mapped_column(String(16), nullable=False, default="normal")  # normal | foil | etched
    # language: Mapped[str] = mapped_column(String(8), nullable=False, default="en")

    name: Mapped[str] = mapped_column(String(512))
    board: Mapped[str] = mapped_column(String(8), default="main")  # main | side

    # Resolved at import: representative printing (display) + oracle id (ownership matching).
    # The printing prefers a copy you own, else a tournament-legal one; the user can override it.
    oracle_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
    # Non-standard-copy markers (independent): a printed proxy vs. a genuine special (art card,
    # alter, misprint, …).
    proxy: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=false())
    special: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=false())
    # True when this card is owned as part of an owned deck (#298): its quantity/printing edits
    # sync to the collection. Set at import (full = all matched; partial = the cards you ticked).
    owned: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=false())

    deck: Mapped[Deck] = relationship(back_populates="cards")
