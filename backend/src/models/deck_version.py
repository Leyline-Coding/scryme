"""Decks: named lists of wanted cards, compared against the owned collection.

A deck card is matched to the collection by ``oracle_id`` (any printing you own counts), so the
"what am I missing" view reflects ownership regardless of which printing is in the list. The
representative ``scryfall_id`` is kept only for display (image/name). Unrecognized lines keep
their text with null ids.
"""

from __future__ import annotations

import datetime
import uuid

from sqlalchemy import DateTime, ForeignKey, Integer, String, false, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship, foreign

from src.db import Base


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
