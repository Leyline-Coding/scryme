"""Foreign-exchange rates (1 USD -> `rate` of `code`) for display-currency conversion (#232).

Scryfall prices are USD/EUR only; other display currencies (GBP/CAD/AUD/JPY) are shown by
converting the USD price with these periodically-refreshed rates (ECB, via Frankfurter). One row
per lowercase ISO currency code.
"""

from __future__ import annotations

import datetime

from sqlalchemy import DateTime, Float, String, func
from sqlalchemy.orm import Mapped, mapped_column

from src.db import Base


class FxRate(Base):
    __tablename__ = "fx_rate"

    code: Mapped[str] = mapped_column(String(3), primary_key=True)  # lowercase ISO, e.g. "gbp"
    rate: Mapped[float] = mapped_column(Float)                      # 1 USD -> this many `code`
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
