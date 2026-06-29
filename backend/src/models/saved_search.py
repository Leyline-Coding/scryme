"""The `saved_search` table: named, reusable searches for the single implicit user.

Stores the query string plus the scope/sort/direction so a saved search restores the exact
result view it was created from.
"""

from __future__ import annotations

import datetime

from sqlalchemy import DateTime, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from src.db import Base


class SavedSearch(Base):
    __tablename__ = "saved_search"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(128), unique=True)
    query: Mapped[str] = mapped_column(Text, default="")
    scope: Mapped[str] = mapped_column(String(16), default="collection")
    sort: Mapped[str] = mapped_column(String(16), default="name")
    direction: Mapped[str] = mapped_column(String(8), default="asc")

    # Alert state (migration 0009): baseline match set (NULL until first evaluated) and the
    # newly-matching ids the user hasn't viewed yet. See src/saved_alerts.py.
    seen_ids: Mapped[list[str] | None] = mapped_column(JSONB, nullable=True, default=None)
    new_ids: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)

    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
