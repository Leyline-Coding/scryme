"""Comprehensive-rules chunks for retrieval-augmented rules Q&A (#196).

One row per rule (a top-level ``NNN.N`` rule with its subrules, or a glossary entry), with an
L2-normalized embedding stored as ``float8[]`` so cosine similarity is a dot product in Python
(no pgvector). Populated by the ``backfill-rules`` CLI when an embedding endpoint is configured.
"""

from __future__ import annotations

import datetime

from sqlalchemy import DateTime, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import ARRAY, DOUBLE_PRECISION
from sqlalchemy.orm import Mapped, mapped_column

from src.db import Base


class RulesChunk(Base):
    __tablename__ = "rules_chunk"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ref: Mapped[str] = mapped_column(String(64), index=True)  # e.g. "702.19 Trample"
    text: Mapped[str] = mapped_column(Text)
    model: Mapped[str] = mapped_column(String(128))
    vector: Mapped[list[float]] = mapped_column(ARRAY(DOUBLE_PRECISION))
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
