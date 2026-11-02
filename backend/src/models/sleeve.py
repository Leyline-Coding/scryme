"""The `sleeves` table: card sleeves that can be associated with decks.
"""

from __future__ import annotations

import datetime
import uuid

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Integer,
    String,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.db import Base


class Sleeve(Base):
    __tablename__ = "sleeves"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    
    # Optional deck association
    deck_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("deck.id", ondelete="SET NULL"))
    
    # Image filename - image stored on disk in data/sleeves/
    # Can be empty if a color is used instead
    image: Mapped[str | None] = mapped_column(String(256), nullable=True)
    
    # Optional color for the sleeve (hex color code like #FF0000)
    color: Mapped[str | None] = mapped_column(String(7), nullable=True)
    
    # Optional name for the sleeve
    name: Mapped[str | None] = mapped_column(String(256))
    
    added_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    deck: Mapped[Deck] = relationship("Deck", back_populates="sleeves")