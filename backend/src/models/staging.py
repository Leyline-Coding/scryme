"""Staging for the two-phase import: a parsed+matched upload awaiting a confirm decision."""

from __future__ import annotations

import datetime
import uuid

from sqlalchemy import DateTime, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.db import Base


class ImportStaging(Base):
    __tablename__ = "import_staging"

    token: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    source_format: Mapped[str | None] = mapped_column(String(32))
    # Serialized matched rows: [{"row": {...ImportRow...}, "scryfall_id": str|None, "method": str}]
    payload: Mapped[list] = mapped_column(JSONB)
