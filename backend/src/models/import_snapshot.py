"""Pre-import collection snapshots (#59): undo a bad import with one click.

Right before a confirmed import applies its merge, the full ``collection_card`` state is serialized
into one row here. "Undo last import" restores it. Only the most recent few are kept.
"""

from __future__ import annotations

import datetime

from sqlalchemy import DateTime, Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from src.db import Base


class ImportSnapshot(Base):
    __tablename__ = "import_snapshot"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    label: Mapped[str] = mapped_column(String(64))         # e.g. "manabox import"
    card_count: Mapped[int] = mapped_column(Integer, default=0)
    # Serialized collection_card rows as they were just before the merge.
    payload: Mapped[list] = mapped_column(JSONB)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
