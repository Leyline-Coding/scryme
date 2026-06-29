"""Custom checklists: a named list of cards you're tracking completion of (e.g. the Power 9).

Like a deck but without quantities — each item is one card, matched to the collection by oracle id
(any printing you own counts). Unrecognized lines keep their text with null ids.
"""

from __future__ import annotations

import datetime
import uuid

from sqlalchemy import DateTime, ForeignKey, Integer, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.db import Base


class Checklist(Base):
    __tablename__ = "checklist"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(256))
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    items: Mapped[list[ChecklistItem]] = relationship(
        back_populates="checklist", cascade="all, delete-orphan", lazy="selectin"
    )


class ChecklistItem(Base):
    __tablename__ = "checklist_item"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    checklist_id: Mapped[int] = mapped_column(
        ForeignKey("checklist.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(512))
    oracle_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
    scryfall_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))

    checklist: Mapped[Checklist] = relationship(back_populates="items")
