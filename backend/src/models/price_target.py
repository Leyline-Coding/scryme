"""Price watchlist targets (#88): alert when a card crosses a USD threshold.

Each row watches one printing (``scryfall_id``) and fires when its market price crosses the
threshold in the chosen direction. ``triggered_at`` / ``triggered_price`` hold the current crossing
state — set when the condition is met, cleared when the price moves back — so the UI can surface
"these are hitting your target right now" and notify when that set grows.
"""

from __future__ import annotations

import datetime
import uuid

from sqlalchemy import DateTime, Float, Integer, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.db import Base


class PriceTarget(Base):
    __tablename__ = "price_target"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    scryfall_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    direction: Mapped[str] = mapped_column(String(8))  # "below" | "above"
    threshold: Mapped[float] = mapped_column(Float)

    triggered_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True))
    triggered_price: Mapped[float | None] = mapped_column(Float)

    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
