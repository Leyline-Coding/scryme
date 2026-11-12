"""Storage locations for collection cards.

A ``Location`` is a physical or logical storage location for collection cards.
It can be a box, binder, or deck with an associated ID reference to the specific
instance of that storage type.
"""

from __future__ import annotations

import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.db import Base


class Location(Base):
    __tablename__ = "location"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    type: Mapped[str] = mapped_column(String(16))  # 'box', 'binder', or 'deck'
    foreign_id: Mapped[int] = mapped_column(Integer)  # ID of the corresponding box/binder/deck
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )