"""Decks: named lists of wanted cards, compared against the owned collection.

A deck card is matched to the collection by ``oracle_id`` (any printing you own counts), so the
"what am I missing" view reflects ownership regardless of which printing is in the list. The
representative ``scryfall_id`` is kept only for display (image/name). Unrecognized lines keep
their text with null ids.
"""

from __future__ import annotations

import datetime
import uuid

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, false, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.db import Base


class Deck(Base):
    __tablename__ = "deck"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(256))
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    # Manual Commander bracket override (1–5). NULL = use the computed estimate (#159).
    bracket_override: Mapped[int | None] = mapped_column(Integer)

    cards: Mapped[list[DeckCard]] = relationship(
        back_populates="deck", cascade="all, delete-orphan", lazy="selectin"
    )


class DeckCard(Base):
    __tablename__ = "deck_card"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    deck_id: Mapped[int] = mapped_column(
        ForeignKey("deck.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(512))
    quantity: Mapped[int] = mapped_column(Integer, default=1)
    board: Mapped[str] = mapped_column(String(8), default="main")  # main | side

    # Resolved at import: representative printing (display) + oracle id (ownership matching).
    # The printing prefers a copy you own, else a tournament-legal one; the user can override it.
    scryfall_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    oracle_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
    # Non-standard-copy markers (independent): a printed proxy vs. a genuine special (art card,
    # alter, misprint, …). Language is the copy's language (Scryfall code, English default) — the
    # card DB is English-only per printing, so this is stored on the line rather than a card row.
    proxy: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=false())
    special: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=false())
    language: Mapped[str] = mapped_column(String(8), nullable=False, server_default="en")

    deck: Mapped[Deck] = relationship(back_populates="cards")


class DeckVersion(Base):
    """An immutable snapshot of a deck's card list at a point in time (#100).

    The snapshot lives in ``cards`` (JSONB) — a list of ``{name, quantity, board, oracle_id,
    scryfall_id}`` — so a version is self-contained and diffing is a pure in-memory operation that
    doesn't depend on the deck's later edits.
    """
    __tablename__ = "deck_version"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    deck_id: Mapped[int] = mapped_column(
        ForeignKey("deck.id", ondelete="CASCADE"), index=True
    )
    label: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    cards: Mapped[list[dict]] = mapped_column(JSONB)
