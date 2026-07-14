"""First-class custom binders + binder groups (#206).

A ``Binder`` is a user-named group of owned cards (many-to-many via ``BinderCard``); binders can be
organized into a ``BinderGroup``. Distinct from the import ``collection_card.binder_name`` string
and from tags. Membership is by printing (``scryfall_id``); ownership is enforced when adding.
"""

from __future__ import annotations

import datetime
import uuid

from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.db import Base


class BinderGroup(Base):
    __tablename__ = "binder_group"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(128), unique=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    binders: Mapped[list[Binder]] = relationship(
        back_populates="group", order_by="Binder.name", lazy="selectin"
    )


class Binder(Base):
    __tablename__ = "binder"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(128), unique=True)
    group_id: Mapped[int | None] = mapped_column(
        ForeignKey("binder_group.id", ondelete="SET NULL"), index=True
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    group: Mapped[BinderGroup | None] = relationship(back_populates="binders")


class BinderCard(Base):
    __tablename__ = "binder_card"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    binder_id: Mapped[int] = mapped_column(
        ForeignKey("binder.id", ondelete="CASCADE"), index=True
    )
    scryfall_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    added_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    __table_args__ = (UniqueConstraint("binder_id", "scryfall_id", name="uq_binder_card"),)
