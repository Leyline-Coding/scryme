"""
"""

from __future__ import annotations

import datetime

from sqlalchemy import DateTime, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from src.db import Base


class NLQCache(Base):
    __tablename__ = "nl_q_cache"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    nl_text: Mapped[str] = mapped_column(String(512), default="", server_default="")
    q_text: Mapped[str | None] = mapped_column(
        String(512), default=None, nullable=True, server_default=None
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
