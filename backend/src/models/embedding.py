"""Per-oracle text embeddings for semantic card similarity (#176).

One row per ``oracle_id`` (embeddings describe a card's rules text, which is identical across
printings), holding an L2-normalized vector so cosine similarity is a plain dot product. Vectors
are stored as a Postgres ``float8[]`` — no pgvector dependency; similarity is computed in Python at
personal-collection scale. ``model`` records which embedding model produced the vector so a model
change can trigger a re-embed.
"""

from __future__ import annotations

import datetime
import uuid

from sqlalchemy import DateTime, Integer, String, func
from sqlalchemy.dialects.postgresql import ARRAY, DOUBLE_PRECISION, UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.db import Base


class CardEmbedding(Base):
    __tablename__ = "card_embedding"

    oracle_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    model: Mapped[str] = mapped_column(String(128))
    dim: Mapped[int] = mapped_column(Integer)
    vector: Mapped[list[float]] = mapped_column(ARRAY(DOUBLE_PRECISION))
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
