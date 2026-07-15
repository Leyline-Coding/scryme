"""The `cards` table: one row per Scryfall printing (from the Default Cards bulk file).

Frequently-searched attributes get dedicated, indexed columns; the complete Scryfall
card object is retained in `raw` (JSONB) so the search engine and UI can reach any
field without a schema migration.
"""

from __future__ import annotations

import datetime
import uuid

from sqlalchemy import Date, Float, Index, String, Text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.db import Base


class Card(Base):
    __tablename__ = "cards"

    # Scryfall's per-printing UUID is the natural primary key.
    scryfall_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    oracle_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)

    name: Mapped[str] = mapped_column(String(512), index=True)
    set_code: Mapped[str] = mapped_column(String(16), index=True)
    set_name: Mapped[str | None] = mapped_column(String(256))
    collector_number: Mapped[str] = mapped_column(String(32))
    rarity: Mapped[str | None] = mapped_column(String(32), index=True)

    mana_cost: Mapped[str | None] = mapped_column(String(128))
    cmc: Mapped[float | None] = mapped_column(Float, index=True)
    type_line: Mapped[str | None] = mapped_column(String(256))
    oracle_text: Mapped[str | None] = mapped_column(Text)
    power: Mapped[str | None] = mapped_column(String(16))
    toughness: Mapped[str | None] = mapped_column(String(16))
    loyalty: Mapped[str | None] = mapped_column(String(16))

    colors: Mapped[list[str] | None] = mapped_column(ARRAY(String(2)))
    color_identity: Mapped[list[str] | None] = mapped_column(ARRAY(String(2)))
    keywords: Mapped[list[str] | None] = mapped_column(ARRAY(String(64)))

    lang: Mapped[str] = mapped_column(String(8), default="en", index=True)
    layout: Mapped[str | None] = mapped_column(String(32))
    released_at: Mapped[datetime.date | None] = mapped_column(Date, index=True)

    legalities: Mapped[dict | None] = mapped_column(JSONB)
    prices: Mapped[dict | None] = mapped_column(JSONB)  # Scryfall: usd (TCGplayer), eur, tix

    # Prices from other marketplaces, keyed by source (#231): e.g.
    # {"cardkingdom": {"usd": "1.23", "usd_foil": "4.56"}, "manapool": {...}}. Populated by the
    # market-price sync (MTGJSON for Card Kingdom, ManaPool API for ManaPool); NULL until synced.
    market_prices: Mapped[dict | None] = mapped_column(JSONB)
    # MTGJSON printing UUID, used to join this printing to MTGJSON's Card Kingdom prices.
    mtgjson_id: Mapped[str | None] = mapped_column(String(64))

    # Local image cache bookkeeping: 'pending' | 'cached' | 'missing' | 'none'.
    image_status: Mapped[str] = mapped_column(String(16), default="pending")

    # Full Scryfall card object for everything not promoted to a column above.
    raw: Mapped[dict] = mapped_column(JSONB)

    __table_args__ = (
        Index("ix_cards_set_collector", "set_code", "collector_number"),
        # GIN indexes (created in migration) accelerate array containment + JSONB lookups.
        Index("ix_cards_colors_gin", "colors", postgresql_using="gin"),
        Index("ix_cards_color_identity_gin", "color_identity", postgresql_using="gin"),
        Index("ix_cards_keywords_gin", "keywords", postgresql_using="gin"),
        Index("ix_cards_raw_gin", "raw", postgresql_using="gin"),
    )
