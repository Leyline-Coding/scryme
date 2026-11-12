"""Storage boxes (#160): a registry of user-named physical boxes.

A box is a physical container for bulk storage, a type of location.
This table just lets users create/rename/delete boxes (including empty ones).
"""

from __future__ import annotations

import datetime

from sqlalchemy import DateTime, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from src.db import Base


class Box(Base):
    __tablename__ = "box"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(128), unique=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
