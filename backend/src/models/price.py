"""Price history: periodic snapshots of the owned collection's value and per-card prices.

A `price_snapshot` records the total collection value at a point in time; `card_price_point` rows
record each owned printing's market USD at that snapshot, so we can compute biggest movers between
two snapshots.
"""

from __future__ import annotations

import datetime
import uuid

from sqlalchemy import DateTime, Float, ForeignKey, Integer, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.db import Base


class PriceSnapshot(Base):
    __tablename__ = "price_snapshot"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    captured_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    total_usd: Mapped[float] = mapped_column(Float, default=0.0)
    card_count: Mapped[int] = mapped_column(Integer, default=0)

    points: Mapped[list[CardPricePoint]] = relationship(
        back_populates="snapshot", cascade="all, delete-orphan"
    )


class CardPricePoint(Base):
    __tablename__ = "card_price_point"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    snapshot_id: Mapped[int] = mapped_column(
        ForeignKey("price_snapshot.id", ondelete="CASCADE"), index=True
    )
    scryfall_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    usd: Mapped[float] = mapped_column(Float)

    snapshot: Mapped[PriceSnapshot] = relationship(back_populates="points")
