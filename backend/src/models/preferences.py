"""Collection-level preferences (#203).

A single row (id == 1) holding the user's collection preferences — the ones that used to live only
in the browser (``localStorage``/cookies): theme/appearance, currency, price source, and search
defaults. Generalizes the ``LLMSettings`` singleton pattern so both the web UI and the planned
mobile apps read one server-side source of truth. When no row exists, ``src.preferences`` falls
back to the operator env defaults (``SCRYME_DEFAULT_*``) and the built-in client defaults, so the
read-only demo works without ever writing.

Server defaults deliberately mirror the current client-side defaults (base.html pre-paint,
_settings.html, search.py) so introducing the row changes nothing until a value is set.

``owner_id``/``collection_id`` are nullable and unused today — cheap forward-compat so a future
multi-collection move (accounts spike #205) is an additive migration, not a rewrite.
"""

from __future__ import annotations

import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, Text, false, func, text, true
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from src.db import Base


class Preferences(Base):
    __tablename__ = "preferences"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)

    # Forward-compat ownership (always NULL today; the singleton stays id == 1).
    owner_id: Mapped[int | None] = mapped_column(Integer, index=True)
    collection_id: Mapped[int | None] = mapped_column(Integer, index=True)

    # Server-read preferences (cookie-backed today; see currency.py / pricing.py / search.py).
    currency: Mapped[str] = mapped_column(String(8), default="usd", server_default="usd")
    price_source: Mapped[str] = mapped_column(String(32), default="tcgplayer",
                                              server_default="tcgplayer")
    search_filter: Mapped[str] = mapped_column(Text, default="", server_default="")
    movers: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=false())
    view: Mapped[str] = mapped_column(String(8), default="grid", server_default="grid")
    page_size: Mapped[int] = mapped_column(Integer, default=60, server_default=text("60"))
    infinite: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=false())
    hist_currency: Mapped[str | None] = mapped_column(String(8))  # NULL = inherit `currency`

    # Appearance preferences (localStorage-backed today; see base.html pre-paint / _settings.html).
    mode: Mapped[str] = mapped_column(String(8), default="dark", server_default="dark")
    palette: Mapped[str] = mapped_column(String(32), default="trop-orange",
                                         server_default="trop-orange")
    # accent "" means "use the palette's default accent".
    accent: Mapped[str] = mapped_column(String(16), default="", server_default="")
    foil_speed: Mapped[int] = mapped_column(Integer, default=6, server_default=text("6"))
    spin: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=true())
    spin_speed: Mapped[int] = mapped_column(Integer, default=6, server_default=text("6"))
    # Grid card size (1–10; the search/collection grid's min column width). 5 == today's 10rem.
    card_size: Mapped[int] = mapped_column(Integer, default=5, server_default=text("5"))

    # Forward-compat bucket for future toggles so we don't migrate per checkbox.
    extra: Mapped[dict] = mapped_column(JSONB, default=dict, server_default=text("'{}'::jsonb"))

    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
