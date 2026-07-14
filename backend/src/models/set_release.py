"""The `set_release` table: Scryfall set metadata for the release calendar (#178).

Synced from Scryfall's `/sets` endpoint (cached ≥24h) so we can show upcoming and recently-released
sets with their dates — independent of which sets we've ingested cards for.
"""

from __future__ import annotations

import datetime

from sqlalchemy import Boolean, Date, DateTime, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from src.db import Base


class SetRelease(Base):
    __tablename__ = "set_release"

    code: Mapped[str] = mapped_column(String(16), primary_key=True)
    name: Mapped[str] = mapped_column(String(256))
    released_at: Mapped[datetime.date | None] = mapped_column(Date)
    set_type: Mapped[str | None] = mapped_column(String(32))
    card_count: Mapped[int] = mapped_column(Integer, default=0)
    digital: Mapped[bool] = mapped_column(Boolean, default=False)
    icon_uri: Mapped[str | None] = mapped_column(String(512))
    synced_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
